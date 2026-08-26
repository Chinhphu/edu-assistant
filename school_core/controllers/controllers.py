# from odoo import http


# class SchoolCore(http.Controller):
#     @http.route('/school_core/school_core', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/school_core/school_core/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('school_core.listing', {
#             'root': '/school_core/school_core',
#             'objects': http.request.env['school_core.school_core'].search([]),
#         })

#     @http.route('/school_core/school_core/objects/<model("school_core.school_core"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('school_core.object', {
#             'object': obj
#         })

