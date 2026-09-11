import base64
import logging
import os
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
    audio_file = fields.Binary(string='File ghi âm', attachment=False, required=True)
    audio_filename = fields.Char(string='Tên file ghi âm')

    @staticmethod
    def _upload_in_background(db_name, wizard_id):
        with Registry(db_name).cursor() as cr:
            try:
                env = Environment(cr, SUPERUSER_ID, {})
                wizard = env['school.media.upload.wizard'].browse(wizard_id)
                audio_data = base64.b64decode(wizard.audio_file)
                audio_suffix = os.path.splitext(wizard.audio_filename or '')[1] or '.mp3'
                video_url = env['school.media.asset']._upload_audio_bytes_to_youtube(
                    audio_data, wizard.name, audio_suffix
                )
                wizard.write({'audio_file': False, 'audio_filename': False})
                _logger.info('Upload audio độc lập lên YouTube thành công: %s', video_url)
            except Exception:
                wizard = env['school.media.upload.wizard'].browse(wizard_id)
                wizard.write({'audio_file': False, 'audio_filename': False})
                _logger.exception('Upload audio độc lập lên YouTube thất bại')

    def action_upload(self):
        self.ensure_one()
        threading.Thread(
            target=type(self)._upload_in_background,
            args=(self.env.cr.dbname, self.id),
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