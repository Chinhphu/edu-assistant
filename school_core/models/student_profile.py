from odoo import fields, models


class StudentProfile(models.Model):
    _name = 'student.profile'
    _description = 'Hồ sơ học sinh'

    name = fields.Char(string='Tên học sinh', required=True)
    student_code = fields.Char(string='Mã học sinh', required=True)
    class_id = fields.Many2one(
        comodel_name='school.class',
        string='Lớp học',
    )
    user_id = fields.Many2one(
        comodel_name='res.users',
        string='Tài khoản Portal',
    )
    avatar = fields.Image(string='Ảnh thẻ')
    dob = fields.Date(string='Ngày sinh')