from odoo import models, fields, api

class StudentProfile(models.Model):
    _name = 'student.profile'
    _description = 'Hồ sơ học sinh'

    _student_code_unique = models.Constraint(
        'unique(student_code)',
        'Mã học sinh phải là duy nhất.',
    )

    name = fields.Char(string='Tên học sinh', required=True)
    student_code = fields.Char(
        string='Mã học sinh',
        required=True,
        readonly=True,
        copy=False,
    )
    class_id = fields.Many2one('school.class', string='Lớp')
    
    user_id = fields.Many2one('res.users', string='Tài khoản Portal', readonly=True)
    
    dob = fields.Date(string='Ngày sinh')

    attribute_ids = fields.One2many(
        'student.attribute',
        'student_id',
        string='Dữ liệu khảo sát',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('student_code'):
                vals['student_code'] = self.env['ir.sequence'].next_by_code(
                    'student.profile'
                )

            if not vals.get('user_id'):
                portal_group = self.env.ref('base.group_portal')
                user_vals = {
                    'name': vals.get('name'),
                    'login': vals.get('student_code'), 
                    'groups_id': [(6, 0, [portal_group.id])],
                    'password': '123' 
                }
                new_user = self.env['res.users'].sudo().create(user_vals)
                
                vals['user_id'] = new_user.id
                
        return super(StudentProfile, self).create(vals_list)