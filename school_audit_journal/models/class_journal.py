import base64
from importlib import import_module
import logging
import os
import subprocess
import tempfile
import threading
import time

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tools import config

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:  # pragma: no cover
    YouTubeTranscriptApi = None

_logger = logging.getLogger(__name__)

class ClassJournal(models.Model):
    _name = 'class.journal'
    _description = 'Nhật ký Tiết dạy'
    _inherit = ['mail.thread']

    name = fields.Char(string='Tên bài dạy', required=True, tracking=True)
    class_id = fields.Many2one('school.class', string='Lớp học', required=True, tracking=True)
    date = fields.Datetime(string='Thời gian dạy', default=fields.Datetime.now, tracking=True)
    
    # 1. Các trường nhận file đầu vào
    audio_file = fields.Binary(string='File Ghi âm', required=True)
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
    def _append_log(self, message):
        self.ensure_one()
        timestamp = fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.message_post(body=f"[{timestamp}] {message}")

    def _append_short_log(self, message):
        self.ensure_one()
        timestamp = fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.message_post(body=f"[{timestamp}] {message}")

    def action_process_and_upload(self):
        self.ensure_one()
        if not self.audio_file:
            raise UserError(_("Vui lòng tải lên file ghi âm trước!"))

        self.state = 'processing'
        self._append_short_log("Bắt đầu xử lý video: render + upload YouTube")

        # Lấy ID và Tên database hiện tại để truyền vào luồng ngầm
        journal_id = self.id
        db_name = self.env.cr.dbname

        threaded_task = threading.Thread(
            target=type(self)._run_upload_background,
            args=(db_name, journal_id),
            name=f'class-journal-upload-{journal_id}',
            daemon=True,
        )
        threaded_task.start()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Đang xử lý',
                'message': 'Hệ thống đã bắt đầu convert audio sang video và upload lên YouTube. Theo dõi tiến độ ở Chatter bên dưới form.',
                'sticky': False,
                'type': 'info',
            }
        }

    @staticmethod
    def _run_upload_background(db_name, journal_id):
        """Upload a journal video using a fresh Odoo environment."""
        Request = getattr(import_module('google.auth.transport.requests'), 'Request')
        Credentials = getattr(import_module('google.oauth2.credentials'), 'Credentials')
        build = getattr(import_module('googleapiclient.discovery'), 'build')
        MediaFileUpload = getattr(
            import_module('googleapiclient.http'), 'MediaFileUpload'
        )

        audio_path = video_path = None
        with Registry(db_name).cursor() as new_cr:
            env = api.Environment(new_cr, SUPERUSER_ID, {})
            journal = env['class.journal'].browse(journal_id)
            
            try:
                audio_data = base64.b64decode(journal.audio_file)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_audio:
                    temp_audio.write(audio_data)
                    audio_path = temp_audio.name
                
                video_path = audio_path.replace('.mp3', '.mp4')

                journal._append_short_log("Đang render video bằng FFmpeg")
                new_cr.commit()  # Lưu trạng thái chat xuống db ngay

                command = [
                    'ffmpeg', '-y', 
                    '-f', 'lavfi', '-i', 'color=c=black:s=1280x720:r=1',
                    '-i', audio_path,
                    '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-c:a', 'aac', '-shortest',
                    video_path
                ]
                subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                journal._append_short_log("Render video xong, đang upload lên YouTube")
                new_cr.commit()

                SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
                client_id = config.get('youtube_client_id') or os.environ.get(
                    'YOUTUBE_CLIENT_ID'
                )
                client_secret = config.get('youtube_client_secret') or os.environ.get(
                    'YOUTUBE_CLIENT_SECRET'
                )
                refresh_token = config.get('youtube_refresh_token') or os.environ.get(
                    'YOUTUBE_REFRESH_TOKEN'
                )
                if not all((client_id, client_secret, refresh_token)):
                    journal._append_log(
                        "Thiếu cấu hình YouTube: YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET hoặc YOUTUBE_REFRESH_TOKEN."
                    )
                    raise RuntimeError(
                        'Thiếu YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET hoặc '
                        'YOUTUBE_REFRESH_TOKEN trong environment của Odoo.'
                    )

                journal._append_short_log("Đã xác thực YouTube, bắt đầu upload video")

                creds = Credentials(
                    token=None,
                    refresh_token=refresh_token,
                    token_uri='https://oauth2.googleapis.com/token',
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=SCOPES,
                )
                creds.refresh(Request())

                youtube = build('youtube', 'v3', credentials=creds)

                body = {
                    'snippet': {
                        'title': f"Audit Log: {journal.name}",
                        'description': 'Tự động tải lên từ Hệ thống Trợ lý Sư phạm Odoo.',
                        'categoryId': '27' # Education
                    },
                    'status': {
                        'privacyStatus': 'unlisted',
                        'selfDeclaredMadeForKids': False
                    }
                }

                media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
                try:
                    request = youtube.videos().insert(
                        part=','.join(body.keys()), body=body, media_body=media
                    )
                    response = request.execute()
                finally:
                    media_file = getattr(media, '_fd', None)
                    if media_file and not media_file.closed:
                        media_file.close()

                video_id = response.get('id')

                journal.write({
                    'youtube_video_id': video_id,
                    'state': 'waiting_sub',
                })
                journal._append_short_log(f"✅ Upload YouTube thành công. Video ID: {video_id}")

            except Exception as e:
                journal.write({'state': 'draft'})
                journal._append_short_log(f"❌ Lỗi xử lý: {str(e)}")
            finally:
                for temporary_path in (audio_path, video_path):
                    if temporary_path and os.path.exists(temporary_path):
                        for attempt in range(5):
                            try:
                                os.remove(temporary_path)
                                break
                            except PermissionError:
                                if attempt == 4:
                                    _logger.warning(
                                        "Could not remove temporary file: %s",
                                        temporary_path,
                                    )
                                else:
                                    time.sleep(0.2)
                new_cr.commit()
            
    # ==========================================
    # NÚT BẤM 2: LẤY PHỤ ĐỀ BẰNG TAY
    # ==========================================
    def action_fetch_transcript(self):
        self.ensure_one()
        if not self.youtube_video_id:
            raise UserError(_("Chưa có ID Video. Bạn phải đợi quá trình Upload hoàn tất."))

        self._append_short_log("Đang lấy phụ đề từ YouTube")

        if YouTubeTranscriptApi is None:
            raise UserError(_("Thư viện lấy phụ đề YouTube chưa được cài đặt trong môi trường Odoo."))

        try:
            if hasattr(YouTubeTranscriptApi, 'fetch'):
                transcript_list = YouTubeTranscriptApi().fetch(
                    self.youtube_video_id,
                    languages=['vi', 'en', 'vi-VN'],
                )
            elif hasattr(YouTubeTranscriptApi, 'get_transcript'):
                transcript_list = YouTubeTranscriptApi.get_transcript(
                    self.youtube_video_id,
                    languages=['vi', 'en', 'vi-VN'],
                )
            else:
                raise AttributeError("Chưa tìm thấy method fetch/get_transcript trong youtube_transcript_api")

            lines = []
            for item in transcript_list:
                text = getattr(item, 'text', None)
                if text is None and hasattr(item, 'to_dict'):
                    text = item.to_dict().get('text')
                if text:
                    lines.append(str(text).strip())

            transcript_text = '\n'.join(lines).strip()

            if not transcript_text:
                raise UserError(_("Video này không có phụ đề để tải về."))

            self.raw_transcript = transcript_text
            self._append_short_log("✅ Đã lấy xong phụ đề từ YouTube")

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Thành công',
                    'message': 'Đã kéo phụ đề từ YouTube về thành công.',
                    'sticky': False,
                    'type': 'success',
                }
            }
        except Exception as exc:
            self._append_short_log(f"❌ Không lấy được phụ đề từ YouTube: {exc}")
            raise UserError(_(f"Không thể kéo phụ đề từ YouTube: {exc}")) from exc

    # ==========================================
    # NÚT BẤM 3: GỌI GEMINI PHÂN TÍCH
    # ==========================================
    def action_analyze_ai(self):
        self.ensure_one()
        if not self.raw_transcript:
            raise UserError(_("Chưa có nội dung phụ đề để AI phân tích. Vui lòng bấm Lấy Phụ đề trước!"))

        self._append_short_log("Bắt đầu AI phân tích nội dung bài dạy")

        # [GIẢ LẬP] Tạm khóa code gọi google-generativeai

        self.state = 'analyzed'
        self._append_short_log("✅ AI phân tích xong")

    # ==========================================
    # NÚT BẤM 4: KHÓA NHẬT KÝ
    # ==========================================
    def action_lock_journal(self):
        self.ensure_one()
        self._append_short_log("Khóa nhật ký thành bằng chứng")
        self.state = 'locked'