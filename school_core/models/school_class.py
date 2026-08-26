from odoo import fields, models


class SchoolClass(models.Model):
    _name = 'school.class'
    _description = 'Lớp học'

    name = fields.Char(string='Tên lớp', required=True)
    academic_year = fields.Char(string='Năm học', required=True)
    teacher_id = fields.Many2one(
        comodel_name='res.users',
        string='Giáo viên phụ trách',
    )
    student_ids = fields.One2many(
        comodel_name='student.profile',
        inverse_name='class_id',
        string='Học sinh',
    )