# from odoo import http


# class SchoolAuditJournal(http.Controller):
#     @http.route('/school_audit_journal/school_audit_journal', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/school_audit_journal/school_audit_journal/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('school_audit_journal.listing', {
#             'root': '/school_audit_journal/school_audit_journal',
#             'objects': http.request.env['school_audit_journal.school_audit_journal'].search([]),
#         })

#     @http.route('/school_audit_journal/school_audit_journal/objects/<model("school_audit_journal.school_audit_journal"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('school_audit_journal.object', {
#             'object': obj
#         })

