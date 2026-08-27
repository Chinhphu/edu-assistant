from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class ClassJournal(models.Model):
    _name = 'class.journal'
    _description = 'Nhật ký Tiết dạy'
    _inherit = ['mail.thread']

    name = fields.Char(string='Tên bài dạy', required=True, tracking=True)
    class_id = fields.Many2one('school.class', string='Lớp học', required=True, tracking=True)
    date = fields.Datetime(string='Thời gian dạy', default=fields.Datetime.now, tracking=True)
    
    # 1. Các trường nhận file đầu vào
    audio_file = fields.Binary(string='File Ghi âm', tracking=True) # Tạm bỏ required=True để test cho lẹ
    audio_filename = fields.Char(string='Tên File Ghi âm')
    cover_image = fields.Binary(string='Ảnh bìa Video')
    
    # 2. Các trường lưu trữ kết quả
    youtube_video_id = fields.Char(string='ID Video YouTube', tracking=True, readonly=True)
    youtube_url = fields.Char(string='Link YouTube', compute='_compute_youtube_url')
    raw_transcript = fields.Text(string='Phụ đề thô', tracking=True)
    
    state = fields.Selection([
        ('draft', 'Bản Nháp'),
        ('processing', 'Đang render & Upload'),
        ('waiting_sub', 'Chờ Phụ đề'),
        ('analyzed', 'Đã Phân tích AI'),
        ('locked', 'Đã Khóa')
    ], string='Trạng thái', default='draft', tracking=True)

    behavior_ids = fields.One2many('student.behavior', 'journal_id', string='Chi tiết Hành vi')

    @api.depends('youtube_video_id')
    def _compute_youtube_url(self):
        for record in self:
            if record.youtube_video_id:
                record.youtube_url = f"https://www.youtube.com/watch?v={record.youtube_video_id}"
            else:
                record.youtube_url = False

    # ==========================================
    # NÚT BẤM 1: CHUYỂN ĐỔI VÀ UPLOAD YOUTUBE
    # ==========================================
    def action_process_and_upload(self):
        self.ensure_one()
        if not self.audio_file:
            raise UserError(_("Vui lòng tải lên file ghi âm trước!"))
            
        # [GIẢ LẬP] Bỏ qua Threading và API. Đổi state ngay lập tức để test UI
        # self.state = 'processing' # Bỏ qua state processing luôn để đi thẳng đến bước chờ sub
        
        # Giả lập đã upload thành công và có ID trả về
        fake_video_id = "dQw4w9WgXcQ" 
        
        self.write({
            'youtube_video_id': fake_video_id,
            'state': 'waiting_sub'
        })
        self.message_post(body=f"[Test UI] Đã mô phỏng tải lên YouTube thành công! Video ID: {fake_video_id}")

    # ==========================================
    # NÚT BẤM 2: LẤY PHỤ ĐỀ BẰNG TAY
    # ==========================================
    def action_fetch_transcript(self):
        self.ensure_one()
        if not self.youtube_video_id:
            raise UserError(_("Chưa có ID Video. Bạn phải đợi quá trình Upload hoàn tất."))

        # [GIẢ LẬP] Comment thư viện youtube_transcript_api lại
        # from youtube_transcript_api import YouTubeTranscriptApi
        # transcript_list = YouTubeTranscriptApi.get_transcript(self.youtube_video_id, languages=['vi'])
        
        # Giả lập dữ liệu trả về
        fake_text = "Chào các em. Hôm nay chúng ta sẽ học bài số 3. Mời bạn An đứng lên đọc bài."
        
        self.raw_transcript = fake_text
        self.message_post(body="[Test UI] Đã mô phỏng kéo phụ đề từ YouTube về thành công!")
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Thành công',
                'message': 'Đã tải xong phụ đề mô phỏng!',
                'sticky': False,
                'type': 'success',
            }
        }

    # ==========================================
    # NÚT BẤM 3: GỌI GEMINI PHÂN TÍCH
    # ==========================================
    def action_analyze_ai(self):
        self.ensure_one()
        if not self.raw_transcript:
            raise UserError(_("Chưa có nội dung phụ đề để AI phân tích. Vui lòng bấm Lấy Phụ đề trước!"))
        
        # [GIẢ LẬP] Tạm khóa code gọi google-generativeai
        
        self.state = 'analyzed'
        self.message_post(body="[Test UI] Đã mô phỏng AI phân tích xong nội dung bài dạy!")

    # ==========================================
    # NÚT BẤM 4: KHÓA NHẬT KÝ
    # ==========================================
    def action_lock_journal(self):
        self.state = 'locked'