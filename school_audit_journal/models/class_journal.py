import json
import logging
import os
import threading
from typing import Any, Iterable, cast

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tools import config

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

_logger = logging.getLogger(__name__)

class ClassJournal(models.Model):
    _name = 'class.journal'
    _description = 'Nhật ký giảng dạy'
    
    # 🌟 ĐÂY LÀ ĐIỂM ĂN TIỀN: Kế thừa AbstractModel ở đây!
    _inherit = ['mail.thread', 'school.media.asset']

    # Các trường đặc thù của Class Journal
    name = fields.Char(string='Tên bài dạy', required=True, tracking=True)
    class_id = fields.Many2one('school.class', string='Lớp', required=True, tracking=True)
    date = fields.Datetime(string='Thời gian giảng dạy', default=fields.Datetime.now, tracking=True)
    
    raw_transcript = fields.Text(string='Phụ đề YouTube', tracking=True)

    state = fields.Selection([
        ('draft', 'Bản Nháp'),
        ('processing', 'Đang render & Upload'),
        ('waiting_sub', 'Chờ Phụ đề'),
        ('analyzed', 'Đã Phân tích AI'),
        ('locked', 'Đã Khóa')
    ], string='Trạng thái', default='draft', tracking=True)

    behavior_ids = fields.One2many('student.behavior', 'journal_id', string='Chi tiết Hành vi')

    # ==========================================
    # OVERRIDE HOOK TỪ MIXIN MEDIA ASSET
    # ==========================================
    def action_process_and_upload(self):
        """Override nút Upload để đổi trạng thái của Journal"""
        self.state = 'processing'
        return super().action_process_and_upload()

    def _media_upload_success_values(self, video_id, youtube_url):
        """Được gọi tự động sau khi Upload YouTube thành công"""
        values = super()._media_upload_success_values(video_id, youtube_url)
        values['state'] = 'waiting_sub'
        return values

    def _media_upload_failure_values(self):
        """Được gọi tự động nếu Upload YouTube thất bại"""
        values = super()._media_upload_failure_values()
        values['state'] = 'draft'
        return values

    def action_use_existing_youtube_link(self):
        """Override nút dùng link có sẵn"""
        res = super().action_use_existing_youtube_link()
        self.state = 'waiting_sub'
        return res

    # ==========================================
    # LOGIC KÉO PHỤ ĐỀ (TRANSCRIPT)
    # ==========================================
    def action_fetch_transcript(self):
        self.ensure_one()
        if self.state not in ('draft', 'waiting_sub'):
            raise UserError(_("Trạng thái hiện tại không cho phép kéo phụ đề."))

        self._append_log("Đang lấy phụ đề từ YouTube...")
        if not self.raw_transcript:
            self.raw_transcript = self._fetch_youtube_transcript_text()
            self._append_log("✅ Đã lấy xong phụ đề từ YouTube")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': 'Thành công', 'message': 'Đã kéo phụ đề, sẵn sàng phân tích AI.', 'sticky': False, 'type': 'success'}
        }

    def _fetch_youtube_transcript_text(self):
        self.ensure_one()
        if not self.youtube_video_id:
            raise UserError(_("Chưa có ID Video YouTube."))
        if YouTubeTranscriptApi is None:
            raise UserError(_("Thiếu thư viện youtube_transcript_api."))

        try:
            transcript_api = YouTubeTranscriptApi()
            fetch_method = getattr(transcript_api, 'fetch', None)
            get_transcript_method = getattr(YouTubeTranscriptApi, 'get_transcript', None)
            if callable(fetch_method):
                transcript_items = fetch_method(
                    self.youtube_video_id,
                    languages=['vi', 'en', 'vi-VN'],
                )
            elif callable(get_transcript_method):
                transcript_items = get_transcript_method(
                    self.youtube_video_id,
                    languages=['vi', 'en', 'vi-VN'],
                )
            else:
                raise AttributeError(
                    'Không tìm thấy API fetch hoặc get_transcript trong youtube_transcript_api.'
                )
            
            def _format_timestamp(seconds):
                m, s = divmod(max(0, int(float(seconds or 0))), 60)
                h, m = divmod(m, 60)
                return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

            lines = []
            lines = []
            for item in transcript_items:
                text = getattr(item, 'text', None)
                start = getattr(item, 'start', None)
                if isinstance(item, dict):
                    text = item.get('text')
                    start = item.get('start')
                text = str(text or '').strip()
                if text:
                    lines.append(f"[{_format_timestamp(start)}] {text}" if start is not None else text)

            res = '\n'.join(lines).strip()
            if not res:
                raise UserError(_("Video không có phụ đề."))
            return res
        except Exception as exc:
            self._append_log(f"❌ Lỗi kéo phụ đề: {exc}")
            raise UserError(_(f"Không thể kéo phụ đề: {exc}")) from exc

    # ==========================================
    # LOGIC PHÂN TÍCH AI (GEMINI)
    # ==========================================
    def action_analyze_ai(self):
        self.ensure_one()
        if self.state not in ('draft', 'waiting_sub', 'analyzed'):
            raise UserError(_("Trạng thái hiện tại không cho phép chạy lại quy trình phân tích."))
        if not self.raw_transcript:
            raise UserError(_("Chưa có nội dung phụ đề. Vui lòng kéo phụ đề trước!"))

        self._append_log("Đưa tiến trình AI vào chạy ngầm để tránh đơ máy...")
        
        threading.Thread(
            target=type(self)._run_ai_analysis_background,
            args=(self.env.cr.dbname, self.id),
            name=f'class-journal-ai-{self.id}',
            daemon=True,
        ).start()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': 'Đang phân tích', 'message': 'AI đang đọc phụ đề dưới nền.', 'sticky': False, 'type': 'info'}
        }

    @staticmethod
    def _run_ai_analysis_background(db_name, journal_id):
        transcript_text = ""
        with Registry(db_name).cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            journal = env['class.journal'].browse(journal_id)
            transcript_text = journal.raw_transcript
            if not transcript_text:
                return
            journal._append_log("Đang gửi dữ liệu cho Gemini 3.5 Flash...")

        try:
            if genai is None:
                raise Exception("Thiếu thư viện google-generativeai.")
            api_key = config.get('gemini_api_key') or os.environ.get('GEMINI_API_KEY')
            if not api_key:
                raise Exception("Chưa cấu hình API key Gemini.")

            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=600000) if types else None,
            )
            
            prompt = f"""
Bạn là trợ lý phân tích giờ học của giáo viên.
Nhiệm vụ: Tìm hành vi tích cực, gây rối và sự cố thiết bị. 
Chỉ trả về JSON hợp lệ. Mỗi phần tử có: student, quote, time.

Transcript:
{transcript_text}

Mẫu JSON:
{{
  "positive_behaviors": [{{"student": "Tên", "quote": "...", "time": "00:15"}}],
  "negative_behaviors": [{{"student": "Tên", "quote": "...", "time": "01:20"}}],
  "equipment_issues": [{{"student": "Tên", "quote": "...", "time": "02:05"}}]
}}
"""
            safety_settings = [
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            ] if types else None

            config_params = types.GenerateContentConfig(
                response_mime_type='application/json',
                safety_settings=safety_settings,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True) if hasattr(types, 'AutomaticFunctionCallingConfig') else None
            ) if types else None

            response = client.models.generate_content(model='gemini-3.5-flash', contents=prompt, config=config_params)
            response_text = getattr(response, 'text', None) or str(response)

        except Exception as e:
            with Registry(db_name).cursor() as cr_err:
                env_err = api.Environment(cr_err, SUPERUSER_ID, {})
                journal_err = env_err['class.journal'].browse(journal_id)
                journal_err._append_log(f"❌ Lỗi khi gọi AI: {e}")
            return

        with Registry(db_name).cursor() as cr2:
            env2 = api.Environment(cr2, SUPERUSER_ID, {})
            journal2 = env2['class.journal'].browse(journal_id)
            try:
                data = journal2._parse_gemini_json(response_text)
                
                env2['student.behavior'].search([('journal_id', '=', journal2.id)]).unlink()
                created_count = journal2._create_behavior_from_ai(data)
                
                journal2.write({'state': 'analyzed'})
                journal2._append_log(f"✅ AI phân tích xong. Đã tạo {created_count} bằng chứng hành vi.")
            except Exception as e_parse:
                journal2._append_log(f"❌ Lỗi xử lý JSON: {e_parse}")

    # ==========================================
    # CÁC HÀM TIỆN ÍCH PARSE & LƯU DB
    # ==========================================
    def _parse_gemini_json(self, text):
        cleaned = (text or '').strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.strip('`')
            if cleaned.lower().startswith('json'):
                cleaned = cleaned[4:].lstrip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start, end = cleaned.find('{'), cleaned.rfind('}')
            if start != -1 and end != -1 and end > start:
                return json.loads(cleaned[start:end + 1])
            raise

    def _time_to_seconds(self, value):
        if not value: return 0
        try:
            text = str(value).strip()
            if ':' not in text: return int(float(text))
            parts = text.split(':')
            if len(parts) == 2: return int(parts[0]) * 60 + int(float(parts[1]))
            if len(parts) == 3: return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
            return 0
        except Exception:
            return 0

    def _find_student_id_by_name(self, student_name):
        name = (student_name or '').strip()
        if not name: return False
        student = self.env['student.profile'].search([('name', '=ilike', name)], limit=1)
        if not student:
            student = self.env['student.profile'].search([('student_code', '=ilike', name)], limit=1)
        return student.id if student else False

    def _create_behavior_from_ai(self, data):
        created_count = 0
        mapping = {'positive_behaviors': 'tich_cuc', 'negative_behaviors': 'tieu_cuc', 'equipment_issues': 'ky_thuat'}

        for key, behavior_type in mapping.items():
            for item in data.get(key, []) or []:
                student_name = item.get('student') or item.get('name') or 'Ẩn danh'
                quote = item.get('quote') or item.get('exact_quote') or ''
                time_value = item.get('time') or item.get('display_time') or '00:00'

                student_id = self._find_student_id_by_name(student_name)
                if not student_id:
                    self._append_log(f"⚠️ Không nhận dạng được học sinh: {student_name}")

                self.env['student.behavior'].create({
                    'journal_id': self.id,
                    'student_id': student_id,
                    'ai_student_label': str(student_name).strip(),
                    'behavior_type': behavior_type,
                    'exact_quote': str(quote).strip(),
                    'display_time': str(time_value).strip(),
                    'timestamp_seconds': self._time_to_seconds(time_value),
                })
                created_count += 1
        return created_count

    def action_lock_journal(self):
        self.ensure_one()
        self._append_log("Khóa nhật ký thành bằng chứng")
        self.state = 'locked'