import frappe
from frappe.utils import today, date_diff

def before_insert(doc, method=None):
    # --- NEW RULE: BLOCK CLOSED MEMBERSHIPS ---
    if doc.customer_group == "Games Member":
        frappe.throw("<b>Enrollment Closed:</b> The 'Games Member' membership is not currently being offered to new members. Please select a different Customer Group.")

    # --- AUTO-CREATE LINKED CUSTOMER ---
    new_customer = frappe.new_doc("Customer")
    # Make sure 'member_name' matches the fieldname on your Club Member form
    new_customer.customer_name = doc.member_name 
    new_customer.customer_group = doc.customer_group
    new_customer.insert(ignore_permissions=True)

    # Link the newly created Customer ID to this Club Member profile
    doc.customer = new_customer.name

def after_insert(doc, method=None):
    valid_member_groups = [
        "Institutional Member", "Institutional Nominee",
        "Other Government Officer", "Private Member", "Railway Life Member",
        "Railway Non Life Member", "Railway Ward Member"
    ]

    if doc.customer_group in valid_member_groups:
        messages = []
        
        # --- 1. GENERATE THE ONE-TIME MEMBERSHIP INVOICE ---
        membership_item = doc.get("membership_plan")
        
        if membership_item:
            si = frappe.new_doc("Sales Invoice")
            si.customer = doc.customer
            si.set_posting_time = 1
            si.append("items", {
                "item_code": membership_item,
                "qty": 1
            })
            si.set_missing_values()
            si.calculate_taxes_and_totals()
            si.insert(ignore_permissions=True)
            messages.append(f"<li>Joining Fee Invoice: <a href='/app/sales-invoice/{si.name}'><b>{si.name}</b></a></li>")

        # --- 2. CALCULATE MEMBER'S AGE ---
        age = 0
        dob = doc.get("date_of_birth")
        
        if dob:
            days = date_diff(today(), dob)
            age = int(days / 365.25)

        # --- 3. THE ABSOLUTE DECISION MATRIX ---
        cgf_plan_name = None
        group = doc.customer_group
        
        non_railway_groups = [
            "Institutional Member", "Institutional Nominee", 
            "Other Government Officer", "Private Member", "Railway Ward Member"
        ]

        if group in non_railway_groups:
            cgf_plan_name = "CGF - Non Railway Member 60+" if age >= 60 else "CGF - Non Railway Member"
        elif group == "Railway Life Member":
            if age >= 80:
                cgf_plan_name = "CGF - Railway Sr Citizen 80+"
            elif age >= 65:
                cgf_plan_name = "CGF - Railway Sr Citizen 65+"
            else:
                cgf_plan_name = "CGF - Railway Life Member"
        elif group == "Railway Non Life Member":
            cgf_plan_name = "CGF - Railway Non Life Member"

        # --- 4. GENERATE THE RECURRING SUBSCRIPTION ---
        if cgf_plan_name:
            sub = frappe.new_doc("Subscription")
            sub.party_type = "Customer"
            sub.party = doc.customer
            sub.custom_member_name = doc.name 
            sub.start_date = doc.get("membership_start_date") or today()
            sub.generate_invoice = 1 
            
            sub.append("plans", {
                "plan": cgf_plan_name,
                "qty": 1
            })
            
            sub.insert(ignore_permissions=True)
            messages.append(f"<li>Recurring Subscription <b>({cgf_plan_name})</b>: <a href='/app/subscription/{sub.name}'>{sub.name}</a></li>")

        # --- 5. SHOW SUCCESS POP-UP ---
        if messages:
            frappe.msgprint(
                f"<b>Onboarding Automation Complete! (Age: {age})</b><br><br><ul>{''.join(messages)}</ul>",
                title="Member Successfully Onboarded",
                indicator="green"
            )

def on_update(doc, method=None):
    # --- LIFECYCLE MANAGEMENT ---
    if doc.has_value_changed("disabled"):
        
        if doc.customer:
            frappe.db.set_value("Customer", doc.customer, "disabled", doc.disabled)
        
        # SCENARIO 1: Officer leaves
        if doc.disabled == 1:
            active_subs = frappe.get_all("Subscription", filters={"party": doc.customer, "status": "Active"})
            for sub in active_subs:
                frappe.db.set_value("Subscription", sub.name, "status", "Paused")
                frappe.msgprint(f"Subscription {sub.name} automatically Paused because member was disabled.")

        # SCENARIO 2: Officer returns
        elif doc.disabled == 0:
            paused_subs = frappe.get_all("Subscription", filters={"party": doc.customer, "status": "Paused"})
            for sub in paused_subs:
                frappe.db.set_value("Subscription", sub.name, "status", "Active")
                frappe.msgprint(f"Subscription {sub.name} automatically Resumed. Welcome back!")
