import base64
import logging
import threading

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError
from odoo.api import Environment
from odoo.modules.registry import Registry

_logger = logging.getLogger(__name__)


class SchoolMediaUploadWizard(models.TransientModel):
    _name = 'school.media.upload.wizard'
    _description = 'Upload audio lên YouTube'

    name = fields.Char(string='Tiêu đề video', required=True, default='Audio upload từ Odoo')
    audio_file = fields.Binary(string='File âm thanh', required=True)
    audio_filename = fields.Char(string='Tên file')

    @staticmethod
    def _upload_in_background(db_name, audio_data, title, audio_suffix):
        with Registry(db_name).cursor() as cr:
            try:
                env = Environment(cr, SUPERUSER_ID, {})
                video_url = env['school.media.asset']._upload_audio_bytes_to_youtube(
                    audio_data, title, audio_suffix
                )
                _logger.info('Upload audio độc lập lên YouTube thành công: %s', video_url)
            except Exception:
                _logger.exception('Upload audio độc lập lên YouTube thất bại')

    def action_upload(self):
        self.ensure_one()
        if not self.audio_file:
            raise UserError(_('Vui lòng chọn file âm thanh.'))

        audio_data = base64.b64decode(self.audio_file)
        audio_suffix = '.' + self.audio_filename.rsplit('.', 1)[-1] if self.audio_filename and '.' in self.audio_filename else '.mp3'
        threading.Thread(
            target=type(self)._upload_in_background,
            args=(self.env.cr.dbname, audio_data, self.name, audio_suffix),
            name='standalone-youtube-upload',
            daemon=True,
        ).start()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Đang xử lý'),
                'message': _('File đang được chuyển đổi và upload lên YouTube.'),
                'sticky': False,
                'type': 'info',
            },
        }