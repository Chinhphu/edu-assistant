import hashlib
import json
import os

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from odoo import api, fields, models, _
from odoo.tools import config
from odoo.exceptions import UserError, ValidationError


class StudentAttributeCategory(models.Model):
    _name = 'student.attribute.category'
    _description = 'Chủ đề khảo sát'
    _order = 'name'

    name = fields.Char(string='Tên chủ đề', required=True)
    code = fields.Char(string='Mã chủ đề', required=True)
    value_type = fields.Selection(
        [
            ('text', 'Văn bản tự do'),
            ('tags', 'Một hoặc nhiều nhãn'),
        ],
        string='Kiểu dữ liệu',
        default='text',
        required=True,
    )
    active = fields.Boolean(default=True)
    description = fields.Text(string='Mô tả')

    _code_unique = models.Constraint(
        'unique(code)',
        'Mã chủ đề phải là duy nhất.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code'):
                vals['code'] = vals['code'].strip().upper()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('code'):
            vals['code'] = vals['code'].strip().upper()
        return super().write(vals)


class StudentAttributeTag(models.Model):
    _name = 'student.attribute.tag'
    _description = 'Nhãn chuẩn hóa cho dữ liệu khảo sát'
    _order = 'category_id, name'

    name = fields.Char(string='Tên nhãn', required=True)
    code = fields.Char(string='Mã nhãn', required=True)
    category_id = fields.Many2one(
        'student.attribute.category',
        string='Chủ đề',
        required=True,
        ondelete='cascade',
    )
    active = fields.Boolean(default=True)
    status = fields.Selection(
        [
            ('candidate', 'Đề xuất'),
            ('approved', 'Đã duyệt'),
            ('rejected', 'Từ chối'),
        ],
        string='Trạng thái',
        default='candidate',
        required=True,
    )
    origin = fields.Selection(
        [
            ('manual', 'Thủ công'),
            ('automatic', 'Tự động'),
        ],
        string='Cách tạo',
        default='manual',
        required=True,
    )

    _code_category_unique = models.Constraint(
        'unique(category_id, code)',
        'Mã nhãn phải duy nhất trong cùng một chủ đề.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code'):
                vals['code'] = vals['code'].strip().upper()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('code'):
            vals['code'] = vals['code'].strip().upper()
        return super().write(vals)


class StudentAttribute(models.Model):
    _name = 'student.attribute'
    _description = 'Dữ liệu khảo sát của học sinh'
    _order = 'recorded_date desc, id desc'

    student_id = fields.Many2one(
        'student.profile',
        string='Học sinh',
        required=True,
        ondelete='cascade',
        index=True,
    )
    category_id = fields.Many2one(
        'student.attribute.category',
        string='Chủ đề',
        required=True,
        ondelete='restrict',
        index=True,
    )
    raw_value = fields.Text(
        string='Câu trả lời nguyên bản',
        help='Giữ lại câu trả lời gốc để có thể rà soát hoặc chuẩn hóa lại.',
    )
    tag_ids = fields.Many2many(
        'student.attribute.tag',
        string='Nhãn đã chuẩn hóa',
        relation='student_attribute_approved_tag_rel',
        column1='attribute_id',
        column2='tag_id',
        domain="[('category_id', '=', category_id), ('status', '=', 'approved')]",
    )
    candidate_tag_ids = fields.Many2many(
        'student.attribute.tag',
        string='Nhãn đề xuất',
        relation='student_attribute_candidate_tag_rel',
        column1='attribute_id',
        column2='tag_id',
        domain="[('category_id', '=', category_id), ('status', '!=', 'rejected')]",
    )
    recorded_date = fields.Date(
        string='Ngày ghi nhận',
        default=fields.Date.context_today,
        required=True,
    )
    source = fields.Selection(
        [
            ('manual', 'Nhập thủ công'),
            ('import', 'Import'),
            ('survey', 'Khảo sát'),
        ],
        string='Nguồn dữ liệu',
        default='manual',
        required=True,
    )
    note = fields.Text(string='Ghi chú xử lý')
    ai_status = fields.Selection(
        [
            ('pending', 'Chưa phân tích'),
            ('suggested', 'Đã có đề xuất'),
            ('error', 'Lỗi phân tích'),
        ],
        string='Trạng thái AI',
        default='pending',
        required=True,
        copy=False,
    )
    ai_fingerprint = fields.Char(copy=False, readonly=True)
    ai_error = fields.Text(string='Lỗi AI', readonly=True, copy=False)

    def _get_ai_fingerprint(self):
        payload = f'{self.category_id.id}|{(self.raw_value or "").strip()}'
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def action_generate_candidate_tags(self):
        records = self.filtered(lambda record: record.raw_value and record.category_id)
        records = records.filtered(
            lambda record: record.ai_fingerprint != record._get_ai_fingerprint()
        )
        if not records:
            raise UserError(_('Không có câu trả lời mới cần phân tích.'))
        if genai is None or types is None:
            raise UserError(_('Thiếu thư viện google-genai trong môi trường Odoo.'))

        api_key = config.get('gemini_api_key') or os.environ.get('GEMINI_API_KEY')
        if not api_key:
            raise UserError(_('Chưa cấu hình API key Gemini.'))

        category_ids = records.mapped('category_id').ids
        existing_tags = self.env['student.attribute.tag'].search([
            ('category_id', 'in', category_ids),
            ('status', '=', 'approved'),
        ])
        category_by_id = {category.id: category.name for category in records.category_id}
        answers = [
            {
                'id': record.id,
                'category': category_by_id[record.category_id.id],
                'answer': record.raw_value.strip(),
            }
            for record in records
        ]
        known_tags = [
            {
                'category': tag.category_id.name,
                'name': tag.name,
                'code': tag.code,
            }
            for tag in existing_tags
        ]
        prompt = f"""
Bạn là bộ chuẩn hóa dữ liệu khảo sát học sinh. Phân tích nhiều câu trả lời mở trong một lần.
Chỉ trả về JSON hợp lệ theo mẫu {{"items": [{{"id": 1, "tags": [{{"name": "...", "code": "..."}}]}}]}}.
Mỗi câu trả lời có thể có 0 đến 5 tag ngắn gọn, có ý nghĩa để thống kê.
Ưu tiên dùng lại tag đã có nếu cùng nghĩa. Chỉ tạo tag mới khi không có tag phù hợp.
Không đưa câu đầy đủ, tên học sinh, cảm xúc chung hoặc từ vô nghĩa thành tag.
Code tag chỉ dùng A-Z, 0-9 và dấu gạch dưới.

Tag đã được duyệt:
{json.dumps(known_tags, ensure_ascii=False)}

Câu trả lời cần phân tích:
{json.dumps(answers, ensure_ascii=False)}
"""
        try:
            client = genai.Client(api_key=api_key)
            config_params = types.GenerateContentConfig(response_mime_type='application/json')
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=prompt,
                config=config_params,
            )
            data = self._parse_ai_json(getattr(response, 'text', '') or '')
            self._save_ai_suggestions(records, data)
        except Exception as exc:
            records.write({'ai_status': 'error', 'ai_error': str(exc)})
            raise UserError(_('Không thể phân tích khảo sát bằng Gemini: %s') % exc) from exc
        return True

    def _parse_ai_json(self, text):
        cleaned = (text or '').strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.strip('`')
            if cleaned.lower().startswith('json'):
                cleaned = cleaned[4:].lstrip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start, end = cleaned.find('{'), cleaned.rfind('}')
            if start == -1 or end <= start:
                raise UserError(_('Gemini không trả về JSON hợp lệ.'))
            return json.loads(cleaned[start:end + 1])

    def _save_ai_suggestions(self, records, data):
        tag_model = self.env['student.attribute.tag']
        records_by_id = {record.id: record for record in records}
        for item in data.get('items', []):
            record = records_by_id.get(item.get('id'))
            if not record:
                continue
            candidate_tags = self.env['student.attribute.tag']
            for tag_data in item.get('tags', []):
                name = str(tag_data.get('name') or '').strip()
                code = str(tag_data.get('code') or '').strip().upper()
                if not name or not code:
                    continue
                tag = tag_model.search([
                    ('category_id', '=', record.category_id.id),
                    ('code', '=', code),
                ], limit=1)
                if not tag:
                    tag = tag_model.create({
                        'name': name,
                        'code': code,
                        'category_id': record.category_id.id,
                        'origin': 'automatic',
                        'status': 'candidate',
                    })
                if tag.status != 'rejected':
                    candidate_tags |= tag
            record.write({
                'candidate_tag_ids': [(6, 0, candidate_tags.ids)],
                'ai_status': 'suggested',
                'ai_fingerprint': record._get_ai_fingerprint(),
                'ai_error': False,
            })

    def action_approve_candidate_tags(self):
        for record in self:
            approved_tags = record.candidate_tag_ids.filtered(
                lambda tag: tag.status != 'rejected'
            )
            approved_tags.write({'status': 'approved'})
            record.tag_ids = [(6, 0, approved_tags.ids)]
        return True

    def action_clear_candidate_tags(self):
        for record in self:
            record.candidate_tag_ids = [(5, 0, 0)]
        return True

    @api.constrains('category_id', 'tag_ids')
    def _check_tag_categories(self):
        for record in self:
            invalid_tags = record.tag_ids.filtered(
                lambda tag: tag.category_id != record.category_id
            )
            if invalid_tags:
                raise ValidationError(
                    'Nhãn chuẩn hóa phải thuộc đúng chủ đề của bản ghi.'
                )