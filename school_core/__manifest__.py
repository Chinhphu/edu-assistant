{
    'name': "School Core - Nền Tảng Sư Phạm",

    'summary': "Lõi hệ thống quản lý danh mục lớp học và hồ sơ học sinh",

    'description': """
        Odoo Edu-Assistant: Hệ Sinh Thái Trợ Lý Sư Phạm
        ================================================
        Module này đóng vai trò là "xương sống" (Core) của toàn bộ hệ thống, cung cấp:
        
        - Quản lý danh sách Lớp học và Niên khóa.
        - Quản lý Hồ sơ Học sinh (Mã số, Tên, Ngày sinh, Avatar).
        - Tự động hóa việc móc nối 1-1 giữa Hồ sơ Học sinh và Tài khoản Portal (res.users).
        
        Đây là nền tảng tĩnh để các module nghiệp vụ khác (Nhật ký tiết dạy, Gamification, Web Portal) 
        kế thừa và phát triển mà không làm phình to cấu trúc dữ liệu.
    """,

    'author': "Lê Tuấn Kiệt",
    'website': "https://github.com/Chinhphu/edu-assistant/tree/main/school_core", # Mốt quăng code lên Github thì dẫn link vô đây

    'category': 'Education', # Đổi thành Education cho ngầu, lúc cài đặt search chữ Edu là ra
    'version': '1.0',

    # Module cốt lõi nên chỉ cần phụ thuộc vào base
    'depends': ['base'],

    'data': [
        # Nhớ mở comment dòng security này ra khi bạn bắt đầu tạo file phân quyền nhé
        'security/ir.model.access.csv',
        'views/school_views.xml',        
    ],
    
    'demo': [
        'demo/demo.xml',
    ],
    
    'application': False, # Để False vì đây là module nền, module Nhật ký mới để True
    'installable': True,
} # type: ignore