{
    'name': 'School Media Pipeline',
    'version': '1.0',
    'category': 'Tools',
    'summary': 'Reusable audio to video and YouTube upload pipeline',
    'depends': ['base', 'mail'],
    'data': [
        'data/media_config_data.xml',
        'security/ir.model.access.csv',
        'views/media_upload_views.xml',
    ],
    'installable': True,
    'application': False,
}
