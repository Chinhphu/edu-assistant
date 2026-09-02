import base64
from importlib import import_module
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterable, cast
from urllib.parse import parse_qs, urlparse

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover
    genai = None
    types = None

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
    _description = 'Nhật ký giảng dạy'
    _inherit = ['mail.thread']

    name = fields.Char(string='Tên bài dạy', required=True, tracking=True)
    class_id = fields.Many2one('school.class', string='Lớp', required=True, tracking=True)
    date = fields.Datetime(string='Thời gian giảng dạy', default=fields.Datetime.now, tracking=True)
    
    # 1. Các trường nhận file đầu vào
    audio_file = fields.Binary(string='File ghi âm')
    audio_filename = fields.Char(string='Tên file ghi âm')
    cover_image = fields.Binary(string='Ảnh bìa video')
    
    # 2. Các trường lưu trữ kết quả
    youtube_video_id = fields.Char(string='ID video YouTube', tracking=True)
    youtube_url = fields.Char(string='Link YouTube', tracking=True, help='Có thể dán link YouTube hoặc chỉ cần ID video.')
    youtube_thumbnail_html = fields.Html(string='Preview video', compute='_compute_youtube_thumbnail_html', sanitize=False)
    raw_transcript = fields.Text(string='Phụ đề YouTube', tracking=True)

    state = fields.Selection([
        ('draft', 'Bản Nháp'),
        ('processing', 'Đang render & Upload'),
        ('waiting_sub', 'Chờ Phụ đề'),
        ('analyzed', 'Đã Phân tích AI'),
        ('locked', 'Đã Khóa')
    ], string='Trạng thái', default='draft', tracking=True)

    behavior_ids = fields.One2many('student.behavior', 'journal_id', string='Chi tiết Hành vi')

    @staticmethod
    def _extract_youtube_video_id(url_value):
        if not url_value:
            return False

        value = str(url_value).strip()
        if not value:
            return False

        if re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
            return value

        parsed = urlparse(value)
        if parsed.netloc:
            if 'youtube.com' in parsed.netloc:
                if parsed.path.startswith('/watch'):
                    params = parse_qs(parsed.query)
                    video_id = params.get('v', [False])[0]
                    if video_id:
                        return video_id
                if parsed.path.startswith('/embed/'):
                    video_id = parsed.path.split('/embed/')[1].split('/')[0]
                    if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                        return video_id
                if parsed.path.startswith('/shorts/'):
                    video_id = parsed.path.split('/shorts/')[1].split('/')[0]
                    if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                        return video_id
                if parsed.path.startswith('/live/'):
                    video_id = parsed.path.split('/live/')[1].split('/')[0]
                    if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                        return video_id
            elif 'youtu.be' in parsed.netloc:
                video_id = parsed.path.strip('/').split('/')[0]
                if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                    return video_id

        match = re.search(r'(?:v=|vi=|youtu\.be/|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})', value)
        if match:
            return match.group(1)

        return False

    def _sync_youtube_reference(self, video_id=None, youtube_url=None):
        if video_id:
            self.youtube_video_id = video_id
            self.youtube_url = youtube_url or f"https://www.youtube.com/watch?v={video_id}"
            return

        value = (youtube_url or self.youtube_url or '').strip()
        if not value:
            self.youtube_video_id = False
            self.youtube_url = False
            return

        extracted_id = self._extract_youtube_video_id(value)
        if not extracted_id:
            self.youtube_video_id = False
            self.youtube_url = value
            return

        self.youtube_video_id = extracted_id
        if not value.startswith('http'):
            self.youtube_url = f"https://www.youtube.com/watch?v={extracted_id}"
        else:
            self.youtube_url = value

    @api.depends('youtube_video_id', 'youtube_url')
    def _compute_youtube_thumbnail_html(self):
        for record in self:
            video_id = record.youtube_video_id or record._extract_youtube_video_id(record.youtube_url or '')
            if not video_id:
                record.youtube_thumbnail_html = False
                continue

            thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            final_url = record.youtube_url or f"https://www.youtube.com/watch?v={video_id}"
            record.youtube_thumbnail_html = (
                f'<a href="{final_url}" target="_blank">'
                f'<img src="{thumbnail_url}" style="max-width:220px; height:auto; border-radius:8px; border:1px solid #ddd;" />'
                f'</a>'
            )

    @api.onchange('youtube_url')
    def _onchange_youtube_url(self):
        for record in self:
            value = (record.youtube_url or '').strip()
            if not value:
                record.youtube_video_id = False
                record.youtube_url = False
                continue

            video_id = record._extract_youtube_video_id(value)
            if not video_id:
                return {
                    'warning': {
                        'title': 'Link YouTube không hợp lệ',
                        'message': 'Vui lòng dán đúng đường link YouTube hoặc chỉ cần nhập ID video có 11 ký tự.',
                    }
                }

            record._sync_youtube_reference(video_id=video_id, youtube_url=value)

    def action_use_existing_youtube_link(self):
        self.ensure_one()
        value = (self.youtube_url or '').strip()
        video_id = self._extract_youtube_video_id(value)
        if not video_id:
            raise UserError(_("Link YouTube không hợp lệ. Vui lòng dán đúng đường link hoặc ID video của YouTube."))

        self._sync_youtube_reference(video_id=video_id, youtube_url=value)
        self.state = 'waiting_sub'
        self._append_log(f"✅ Đã gán video YouTube từ link: {self.youtube_url}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Đã nhận link YouTube',
                'message': 'Video YouTube đã được thiết lập. Bạn có thể kéo phụ đề và phân tích AI ngay.',
                'sticky': False,
                'type': 'success',
            }
        }

    # ==========================================
    # NÚT BẤM 1: CHUYỂN ĐỔI VÀ UPLOAD YOUTUBE
    # ==========================================
    def _append_log(self, message):
        self.ensure_one()
        timestamp = fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _logger.info("class.journal[%s] %s", self.id, message)
        self.message_post(body=f"[{timestamp}] {message}")

    def action_process_and_upload(self):
        self.ensure_one()
        if not self.audio_file:
            raise UserError(_("Vui lòng tải lên file ghi âm trước khi dùng luồng render video lên YouTube!"))

        self.state = 'processing'
        self._append_log("Bắt đầu xử lý video: render + upload YouTube")

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

                journal._append_log("Đang render video bằng FFmpeg")
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

                journal._append_log("Render video xong, đang upload lên YouTube")
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

                journal._append_log("Đã xác thực YouTube, bắt đầu upload video")

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
                youtube_url = f"https://www.youtube.com/watch?v={video_id}"

                journal.write({
                    'youtube_video_id': video_id,
                    'youtube_url': youtube_url,
                    'state': 'waiting_sub',
                })
                journal._append_log(f"✅ Upload YouTube thành công. Link: {youtube_url}")
                journal.invalidate_recordset()

                # Chỉ refresh form khi đã có Link YouTube và ID video thật sự xuất hiện.
                try:
                    if journal.youtube_video_id and journal.youtube_url:
                        env.cr.execute('SELECT 1')
                except Exception:
                    pass

            except Exception as e:
                journal.write({'state': 'draft'})
                journal._append_log(f"❌ Lỗi xử lý: {str(e)}")
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
    # NÚT BẤM 2 + 3: KÉO PHỤ ĐỀ VÀ AI PHÂN TÍCH
    # ==========================================
    def _fetch_youtube_transcript_text(self):
        self.ensure_one()
        if not self.youtube_video_id:
            raise UserError(_("Chưa có ID Video. Bạn phải đợi quá trình Upload hoàn tất."))

        if YouTubeTranscriptApi is None:
            raise UserError(_("Thư viện lấy phụ đề YouTube chưa được cài đặt trong môi trường Odoo."))

        try:
            transcript_api = YouTubeTranscriptApi()
            fetch_method = getattr(transcript_api, 'fetch', None)
            get_transcript_method = getattr(transcript_api, 'get_transcript', None)
            transcript_list: list[Any] = []

            if callable(fetch_method):
                fetched_data = fetch_method(
                    self.youtube_video_id,
                    languages=['vi', 'en', 'vi-VN'],
                )
                transcript_list = list(cast(Iterable[Any], fetched_data))
            elif callable(get_transcript_method):
                fetched_data = get_transcript_method(
                    self.youtube_video_id,
                    languages=['vi', 'en', 'vi-VN'],
                )
                transcript_list = list(cast(Iterable[Any], fetched_data))
            else:
                raise AttributeError("Chưa tìm thấy method fetch/get_transcript trong youtube_transcript_api")

            def _format_timestamp(seconds):
                total_seconds = max(0, int(float(seconds or 0)))
                minutes, secs = divmod(total_seconds, 60)
                hours, minutes = divmod(minutes, 60)
                if hours:
                    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
                return f"{minutes:02d}:{secs:02d}"

            lines = []
            for item in transcript_list:
                text = getattr(item, 'text', None)
                start = getattr(item, 'start', None)
                item_dict = None
                item_to_dict = getattr(item, 'to_dict', None)
                if callable(item_to_dict):
                    item_dict = item_to_dict()
                    if isinstance(item_dict, dict):
                        if text is None:
                            text = item_dict.get('text')
                        if start is None:
                            start = item_dict.get('start')

                if text:
                    text = str(text).strip()
                    if start is not None:
                        lines.append(f"[{_format_timestamp(start)}] {text}")
                    else:
                        lines.append(text)

            transcript_text = '\n'.join(lines).strip()
            if not transcript_text:
                raise UserError(_("Video này không có phụ đề để tải về."))

            return transcript_text
        except Exception as exc:
            self._append_log(f"❌ Không lấy được phụ đề từ YouTube: {exc}")
            raise UserError(_(f"Không thể kéo phụ đề từ YouTube: {exc}")) from exc

    def _parse_gemini_json(self, text):
        cleaned = (text or '').strip()
        if not cleaned:
            raise ValueError("Gemini trả về nội dung rỗng")

        if cleaned.startswith('```'):
            cleaned = cleaned.strip('`')
            if cleaned.lower().startswith('json'):
                cleaned = cleaned[4:].lstrip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start != -1 and end != -1 and end > start:
                return json.loads(cleaned[start:end + 1])
            raise

    def _time_to_seconds(self, value):
        if not value:
            return 0
        try:
            if isinstance(value, (int, float)):
                return int(value)
            text = str(value).strip()
            if ':' not in text:
                return int(float(text))
            parts = text.split(':')
            if len(parts) == 2:
                minutes, seconds = parts
                return int(minutes) * 60 + int(float(seconds))
            if len(parts) == 3:
                hours, minutes, seconds = parts
                return int(hours) * 3600 + int(minutes) * 60 + int(float(seconds))
            return 0
        except Exception:
            return 0

    def _find_student_id_by_name(self, student_name):
        name = (student_name or '').strip()
        if not name:
            return False

        student = self.env['student.profile'].search([
            ('name', '=ilike', name)
        ], limit=1)
        if student:
            return student.id

        student = self.env['student.profile'].search([
            ('student_code', '=ilike', name)
        ], limit=1)
        if student:
            return student.id

        return False

    def _create_behavior_from_ai(self, data):
        created_count = 0
        mapping = {
            'positive_behaviors': 'tich_cuc',
            'negative_behaviors': 'tieu_cuc',
            'equipment_issues': 'ky_thuat',
        }

        for key, behavior_type in mapping.items():
            for item in data.get(key, []) or []:
                student_name = item.get('student') or item.get('name') or item.get('học_sinh') or 'Ẩn danh'
                quote = item.get('quote') or item.get('exact_quote') or item.get('text') or ''
                time_value = item.get('time') or item.get('timestamp') or item.get('display_time') or '00:00'

                student_id = self._find_student_id_by_name(student_name)
                
                if not student_id:
                    self._append_log(
                        f"⚠️ Không nhận dạng được học sinh, để trống trường Học sinh: {student_name}"
                    )

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

    # ==========================================
    # CHỈ KÉO PHỤ ĐỀ (KHÔNG GỌI AI Ở ĐÂY NỮA)
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
            'params': {
                'title': 'Thành công',
                'message': 'Đã kéo phụ đề từ YouTube. Bạn có thể kiểm tra chữ, sửa lỗi chính tả rồi mới bấm Phân tích AI.',
                'sticky': False,
                'type': 'success',
            }
        }

    # ==========================================
    # GỌI LUỒNG NGẦM AI
    # ==========================================
    def action_analyze_ai(self):
        self.ensure_one()
        if self.state not in ('draft', 'waiting_sub', 'analyzed'):
            raise UserError(_("Trạng thái hiện tại không cho phép chạy lại quy trình phân tích."))
        if not self.raw_transcript:
            raise UserError(_("Chưa có nội dung phụ đề. Vui lòng kéo phụ đề trước!"))

        self._append_log("Đưa tiến trình AI vào chạy ngầm để tránh đơ máy...")
        
        db_name = self.env.cr.dbname
        journal_id = self.id
        
        threaded_task = threading.Thread(
            target=type(self)._run_ai_analysis_background,
            args=(db_name, journal_id),
            name=f'class-journal-ai-{journal_id}',
            daemon=True,
        )
        threaded_task.start()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Đang phân tích',
                'message': 'AI đang đọc phụ đề dưới nền. Bạn có thể đi uống nước, kết quả sẽ tự động hiện lên Chatter.',
                'sticky': False,
                'type': 'info',
            }
        }

    # ==========================================
    # HÀM AI CHẠY NGẦM (KIẾN TRÚC KHÔNG TREO DB)
    # ==========================================
    @staticmethod
    def _run_ai_analysis_background(db_name, journal_id):
        """Hàm AI chạy ngầm BẤT TỬ - Không block DB, không timeout"""
        _logger.info("class.journal[%s] AI background task started", journal_id)
        
        # BƯỚC 1: Lấy dữ liệu & Đóng DB ngay lập tức
        transcript_text = ""
        with Registry(db_name).cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            journal = env['class.journal'].browse(journal_id)
            
            transcript_text = journal.raw_transcript
            if not transcript_text:
                journal.write({'state': 'waiting_sub'})
                journal._append_log("❌ Lỗi: Không tìm thấy phụ đề để phân tích.")
                return

            journal._append_log("Đang gửi dữ liệu cho Gemini (Tiến trình chạy ngầm, vui lòng đợi...)")
            _logger.info(
                "class.journal[%s] transcript loaded (%s characters)",
                journal_id,
                len(transcript_text),
            )
            # Thoát khối 'with' là Database tự động commit và ngắt kết nối an toàn!

        # BƯỚC 2: Gọi AI (Trạng thái tự do, Database đang được nghỉ ngơi)
        try:
            if genai is None:
                raise Exception("Thư viện google-generativeai chưa được cài đặt.")

            gemini_api_key = config.get('gemini_api_key') or os.environ.get('GEMINI_API_KEY')
            if not gemini_api_key:
                raise Exception("Chưa cấu hình API key Gemini trong odoo.conf.")

            # Keep the external request out of the Odoo transaction.
            client = genai.Client(
                api_key=gemini_api_key,
                http_options=types.HttpOptions(timeout=300000) if types else None,
            )
            
            prompt = f"""
Bạn là trợ lý phân tích giờ học của giáo viên.
Nhiệm vụ:
- Đọc phụ đề có timestamp dưới đây.
- Tóm tắt nội dung bài dạy ngắn gọn.
- Tìm các hành vi tích cực của học sinh.
- Tìm các hành vi gây rối hoặc mất tập trung.
- Tìm các sự cố kỹ thuật liên quan đến thiết bị.
- Chỉ trả về JSON hợp lệ, không thêm văn bản ngoài JSON.
- Mỗi phần tử cần có: student, quote, time
- student phải là tên học sinh hoặc mã học sinh nếu biết.
- time phải theo định dạng như 00:15 hoặc 01:32.

Transcript:
{transcript_text}

Trả về JSON theo mẫu sau:
{{
  "summary": "...",
  "positive_behaviors": [{{"student": "Tên học sinh", "quote": "...", "time": "00:15"}}],
  "negative_behaviors": [{{"student": "Tên học sinh", "quote": "...", "time": "01:20"}}],
  "equipment_issues": [{{"student": "Tên học sinh", "quote": "...", "time": "02:05"}}]
}}
"""
            safety_settings = [
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                ),
            ] if types else None

            generation_config = types.GenerateContentConfig(
                response_mime_type='application/json',
                safety_settings=safety_settings
            ) if types else None
            
            # Khúc này AI có thể mất 30s - 1 phút để chạy, nhưng Odoo không hề bị kẹt!
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=prompt,
                config=generation_config,
            )
            response_text = getattr(response, 'text', None) or str(response)
            _logger.info("class.journal[%s] Gemini response received", journal_id)

        except Exception as e:
            _logger.exception("class.journal[%s] Gemini request failed", journal_id)
            # Report the external-service failure without touching the failed AI request transaction.
            with Registry(db_name).cursor() as cr_err:
                env_err = api.Environment(cr_err, SUPERUSER_ID, {})
                journal_err = env_err['class.journal'].browse(journal_id)
                journal_err.write({'state': 'waiting_sub'})
                journal_err._append_log(f"❌ Lỗi khi gọi Gemini AI: {e}")
            return

        # BƯỚC 3: Có kết quả, mở DB ghi vào Odoo
        with Registry(db_name).cursor() as cr2:
            env2 = api.Environment(cr2, SUPERUSER_ID, {})
            journal2 = env2['class.journal'].browse(journal_id)
            
            try:
                data = journal2._parse_gemini_json(response_text)
                summary = data.get('summary') or 'Không có tóm tắt'
                
                existing_behaviors = env2['student.behavior'].search([('journal_id', '=', journal2.id)])
                if existing_behaviors:
                    existing_behaviors.unlink()

                created_count = journal2._create_behavior_from_ai(data)
                
                journal2.write({'state': 'analyzed'})
                journal2._append_log(f"✅ Tóm tắt AI: {summary}")
                journal2._append_log(f"✅ AI phân tích xong. Đã tạo {created_count} bằng chứng hành vi.")
                _logger.info(
                    "class.journal[%s] AI background task completed with %s behaviors",
                    journal_id,
                    created_count,
                )
            except Exception as e_parse:
                journal2.write({'state': 'waiting_sub'})
                journal2._append_log(f"❌ Lỗi xử lý kết quả JSON từ AI: {e_parse}")

    # ==========================================
    # NÚT BẤM 4: KHÓA NHẬT KÝ
    # ==========================================
    def action_lock_journal(self):
        self.ensure_one()
        self._append_log("Khóa nhật ký thành bằng chứng")
        self.state = 'locked'