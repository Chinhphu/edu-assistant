{
    'name': 'Nhật ký giảng dạy',
    'version': '1.0',
    'sequence': 1,
    'category': 'Education',
    'summary': 'Quản lý bằng chứng, phụ đề và phân tích hành vi học sinh bằng AI',
    'depends': ['base', 'mail', 'school_core'], # Bắt buộc phải có school_core
    'data': [
        'security/ir.model.access.csv',
        'views/journal_views.xml',
    ],
    'installable': True,
    'application': True,
}