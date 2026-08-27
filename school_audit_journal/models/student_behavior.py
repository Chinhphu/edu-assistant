from odoo import models, fields, api

class StudentBehavior(models.Model):
    _name = 'student.behavior'
    _description = 'Chi tiết hành vi học sinh'

    journal_id = fields.Many2one('class.journal', string='Tiết dạy', ondelete='cascade')
    
    # Móc sang school_core
    student_id = fields.Many2one('student.profile', string='Học sinh', required=True)
    
    behavior_type = fields.Selection([
        ('tich_cuc', 'Phát biểu / Tích cực'),
        ('tieu_cuc', 'Gây rối / Mất tập trung'),
        ('ky_thuat', 'Lỗi thiết bị')
    ], string='Phân loại')
    
    exact_quote = fields.Char(string='Trích dẫn nguyên văn')
    display_time = fields.Char(string='Thời gian (VD: 14:25)')
    timestamp_seconds = fields.Integer(string='Giây thứ n')

    # Vũ khí tối thượng: Tự động nối link YouTube với tham số thời gian
    video_url_at_time = fields.Char(string='Link bằng chứng', compute='_compute_video_url')

    @api.depends('journal_id.youtube_url', 'timestamp_seconds')
    def _compute_video_url(self):
        for rec in self:
            if rec.journal_id.youtube_url and rec.timestamp_seconds:
                # Nối chuỗi thêm &t=số_giây (hoặc ?t=số_giây tùy định dạng link)
                base_url = rec.journal_id.youtube_url
                separator = '&' if '?' in base_url else '?'
                rec.video_url_at_time = f"{base_url}{separator}t={rec.timestamp_seconds}s"
            else:
                rec.video_url_at_time = False