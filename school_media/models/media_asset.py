import base64
from importlib import import_module
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tools import config

_logger = logging.getLogger(__name__)

# ĐỔI THÀNH AbstractModel ĐỂ LÀM MIXIN
class SchoolMediaAsset(models.AbstractModel):
    _name = 'school.media.asset'
    _description = 'Media Asset Mixin (Audio to YouTube)'

    # Các trường Media xài chung
    audio_file = fields.Binary(string='File ghi âm')
    audio_filename = fields.Char(string='Tên file ghi âm')
    cover_image = fields.Binary(string='Ảnh bìa video')
    
    youtube_video_id = fields.Char(string='ID video YouTube', tracking=True)
    youtube_url = fields.Char(string='Link YouTube', tracking=True, help='Có thể dán link YouTube hoặc chỉ cần ID video.')
    youtube_thumbnail_html = fields.Html(string='Preview video', compute='_compute_youtube_thumbnail_html', sanitize=False)
    
    media_state = fields.Selection([
        ('draft', 'Bản nháp'),
        ('processing', 'Đang render và upload'),
        ('ready', 'Đã upload'),
    ], string='Trạng thái media', default='draft', tracking=True)

    @staticmethod
    def _extract_youtube_video_id(url_value):
        if not url_value:
            return False
        value = str(url_value).strip()
        if re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
            return value

        parsed = urlparse(value)
        if parsed.netloc:
            if 'youtube.com' in parsed.netloc:
                if parsed.path.startswith('/watch'):
                    video_id = parse_qs(parsed.query).get('v', [False])[0]
                    if video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                        return video_id
                for prefix in ('/embed/', '/shorts/', '/live/'):
                    if parsed.path.startswith(prefix):
                        video_id = parsed.path.split(prefix, 1)[1].split('/')[0]
                        if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                            return video_id
            elif 'youtu.be' in parsed.netloc:
                video_id = parsed.path.strip('/').split('/')[0]
                if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                    return video_id

        match = re.search(r'(?:v=|vi=|youtu\.be/|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})', value)
        return match.group(1) if match else False

    def _sync_youtube_reference(self, video_id=None, youtube_url=None):
        self.ensure_one()
        value = (youtube_url or self.youtube_url or '').strip()
        video_id = video_id or self._extract_youtube_video_id(value)
        if not video_id:
            self.youtube_video_id = False
            self.youtube_url = value or False
            return
        self.youtube_video_id = video_id
        self.youtube_url = value if value.startswith('http') else f'https://www.youtube.com/watch?v={video_id}'

    @api.depends('youtube_video_id', 'youtube_url')
    def _compute_youtube_thumbnail_html(self):
        for record in self:
            video_id = record.youtube_video_id or self._extract_youtube_video_id(record.youtube_url or '')
            if not video_id:
                record.youtube_thumbnail_html = False
                continue
            thumbnail_url = f'https://img.youtube.com/vi/{video_id}/hqdefault.jpg'
            final_url = record.youtube_url or f'https://www.youtube.com/watch?v={video_id}'
            record.youtube_thumbnail_html = (
                f'<a href="{final_url}" target="_blank">'
                f'<img src="{thumbnail_url}" style="max-width:220px; height:auto; border-radius:8px; border:1px solid #ddd;" /></a>'
            )

    @api.onchange('youtube_url')
    def _onchange_youtube_url(self):
        for record in self:
            value = (record.youtube_url or '').strip()
            if not value:
                record.youtube_video_id = False
                continue
            video_id = self._extract_youtube_video_id(value)
            if not video_id:
                return {'warning': {'title': 'Link YouTube không hợp lệ', 'message': 'Vui lòng dán đúng link YouTube hoặc ID 11 ký tự.'}}
            record._sync_youtube_reference(video_id=video_id, youtube_url=value)

    def _append_log(self, message):
        self.ensure_one()
        timestamp = fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _logger.info('[%s:%s] %s', self._name, self.id, message)
        self.message_post(body=f'[{timestamp}] {message}')

    def action_use_existing_youtube_link(self):
        self.ensure_one()
        video_id = self._extract_youtube_video_id(self.youtube_url or '')
        if not video_id:
            raise UserError(_('Link YouTube không hợp lệ.'))
        self._sync_youtube_reference(video_id=video_id)
        self.media_state = 'ready'
        self._append_log(f'✅ Đã gán video YouTube: {self.youtube_url}')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': 'Thành công', 'message': 'Video YouTube đã được thiết lập.', 'sticky': False, 'type': 'success'}
        }

    # Hook functions để các class con (như class_journal) có thể override
    def _media_upload_success_values(self, video_id, youtube_url):
        return {'youtube_video_id': video_id, 'youtube_url': youtube_url, 'media_state': 'ready'}

    def _media_upload_failure_values(self):
        return {'media_state': 'draft'}

    def action_process_and_upload(self):
        self.ensure_one()
        if not self.audio_file:
            raise UserError(_('Vui lòng tải lên file ghi âm trước khi upload.'))
        self.media_state = 'processing'
        self._append_log('Bắt đầu render video và upload YouTube (chạy ngầm)')
        
        threading.Thread(
            target=type(self)._run_upload_background,
            args=(self._name, self.env.cr.dbname, self.id),
            name=f'media-upload-{self.id}',
            daemon=True,
        ).start()
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': 'Đang xử lý', 'message': 'Đang render và upload ở tiến trình nền.', 'sticky': False, 'type': 'info'}
        }

    @staticmethod
    def _run_upload_background(model_name, db_name, asset_id):
        Request = getattr(import_module('google.auth.transport.requests'), 'Request')
        Credentials = getattr(import_module('google.oauth2.credentials'), 'Credentials')
        build = getattr(import_module('googleapiclient.discovery'), 'build')
        MediaFileUpload = getattr(import_module('googleapiclient.http'), 'MediaFileUpload')
        audio_path = video_path = None

        with Registry(db_name).cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            asset = env[model_name].browse(asset_id)
            try:
                audio_data = base64.b64decode(asset.audio_file)
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as temp_audio:
                    temp_audio.write(audio_data)
                    audio_path = temp_audio.name
                video_path = audio_path.replace('.mp3', '.mp4')
                
                asset._append_log('Đang render video bằng FFmpeg')
                cr.commit()
                
                subprocess.run([
                    'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=1280x720:r=1', '-i', audio_path,
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', video_path,
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                asset._append_log('Render xong, bắt đầu upload lên YouTube')
                cr.commit()
                
                client_id = config.get('youtube_client_id') or os.environ.get('YOUTUBE_CLIENT_ID')
                client_secret = config.get('youtube_client_secret') or os.environ.get('YOUTUBE_CLIENT_SECRET')
                refresh_token = config.get('youtube_refresh_token') or os.environ.get('YOUTUBE_REFRESH_TOKEN')
                
                if not all((client_id, client_secret, refresh_token)):
                    raise RuntimeError('Thiếu cấu hình YouTube trong odoo.conf.')

                creds = Credentials(
                    token=None, refresh_token=refresh_token, token_uri='https://oauth2.googleapis.com/token',
                    client_id=client_id, client_secret=client_secret, scopes=['https://www.googleapis.com/auth/youtube.upload'],
                )
                creds.refresh(Request())
                youtube = build('youtube', 'v3', credentials=creds)
                media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
                
                try:
                    response = youtube.videos().insert(
                        part='snippet,status',
                        body={
                            'snippet': {'title': f'Audio: {getattr(asset, "name", "Asset")}', 'description': 'Tự động tải lên từ Odoo.', 'categoryId': '27'},
                            'status': {'privacyStatus': 'unlisted', 'selfDeclaredMadeForKids': False},
                        },
                        media_body=media,
                    ).execute()
                finally:
                    media_file = getattr(media, '_fd', None)
                    if media_file and not media_file.closed:
                        media_file.close()

                video_id = response.get('id')
                youtube_url = f'https://www.youtube.com/watch?v={video_id}'
                
                # Gọi hook update values
                asset.write(asset._media_upload_success_values(video_id, youtube_url))
                asset._append_log(f'✅ Upload YouTube thành công: {youtube_url}')
                
            except Exception as exc:
                cr.rollback()
                asset = env[model_name].browse(asset_id)
                asset.write(asset._media_upload_failure_values())
                asset._append_log(f'❌ Lỗi xử lý media: {exc}')
            finally:
                for temporary_path in (audio_path, video_path):
                    if temporary_path and os.path.exists(temporary_path):
                        for _ in range(5):
                            try:
                                os.remove(temporary_path)
                                break
                            except PermissionError:
                                time.sleep(0.2)
                cr.commit()