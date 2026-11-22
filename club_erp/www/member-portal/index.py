import frappe

def get_context(context):
    user = frappe.session.user
    if user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/member-portal"
        raise frappe.Redirect

    from club_erp.api.member_portal import get_member_portal_data
    data = get_member_portal_data()
    context.portal_data = data
    context.title = "ROC Member Services Portal"
