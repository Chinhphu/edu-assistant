from odoo import models, fields, api

class StudentBehavior(models.Model):
    _name = 'student.behavior'
    _description = 'Bằng chứng hành vi'

    journal_id = fields.Many2one('class.journal', string='Nhật ký giảng dạy', ondelete='cascade')
    
    # Móc sang school_core
    student_id = fields.Many2one('student.profile', string='Học sinh')
    ai_student_label = fields.Char(string='Tên AI nhận diện', readonly=True)
    
    behavior_type = fields.Selection([
        ('tich_cuc', 'Phát biểu / tích cực'),
        ('tieu_cuc', 'Gây rối / mất tập trung'),
        ('ky_thuat', 'Lỗi thiết bị')
    ], string='Loại hành vi')
    
    exact_quote = fields.Char(string='Trích dẫn')
    display_time = fields.Char(string='Thời gian hiển thị')
    timestamp_seconds = fields.Integer(string='Giây')

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