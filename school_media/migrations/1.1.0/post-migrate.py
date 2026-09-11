from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    for model_name, model in env.registry.models.items():
        if model._abstract or 'audio_file' not in model._fields:
            continue
        records = env[model_name].with_context(active_test=False).search([])
        if records:
            records.write({'audio_file': False})
