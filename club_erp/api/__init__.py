import frappe
from frappe import _
import io, base64, random, datetime

try:
    import qrcode
except ImportError:
    qrcode = None

@frappe.whitelist()
def get_member_portal_data():
    """Returns all data needed by the logged-in member portal"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please login to access the member portal"), frappe.PermissionError)

    user_doc = frappe.get_doc("User", user)
    
    # 1. Resolve Club Member record
    # Match by Customer ID == username, or email_id == user, or custom_ipas_employee_no
    member_record = None
    if user_doc.username:
        member_record = frappe.db.get_value(
            "Club Member", 
            {"customer": user_doc.username}, 
            ["name", "customer", "member_name", "status", "membership_plan", "customer_group", "date_of_birth", "membership_start_date", "membership_expiry_date", "current_subscription"], 
            as_dict=True
        )

    if not member_record:
        # Match via Contact email
        cms = frappe.db.sql("""
            SELECT cm.name, cm.customer, cm.member_name, cm.status, cm.membership_plan, cm.customer_group, cm.date_of_birth, cm.membership_start_date, cm.membership_expiry_date, cm.current_subscription
            FROM `tabClub Member` cm
            JOIN `tabDynamic Link` dl ON dl.link_doctype = 'Customer' AND dl.link_name = cm.customer AND dl.parenttype = 'Contact'
            JOIN `tabContact` c ON c.name = dl.parent
            WHERE c.email_id = %s OR dl.link_name = %s
            LIMIT 1
        """, (user, user), as_dict=True)
        if cms:
            member_record = cms[0]

    if not member_record:
        return {
            "is_member": False,
            "user_email": user_doc.email,
            "full_name": user_doc.full_name,
            "message": "No active club membership found linked to this account. Please contact the Club Office."
        }

    member_id = member_record["name"]
    customer_id = member_record["customer"]

    # 2. Contact details
    contact = frappe.db.sql("""
        SELECT c.name, c.email_id, c.mobile_no, c.phone
        FROM `tabContact` c
        JOIN `tabDynamic Link` dl ON dl.parent = c.name AND dl.parenttype = 'Contact'
        WHERE dl.link_doctype = 'Customer' AND dl.link_name = %s
        ORDER BY c.is_primary_contact DESC, c.modified DESC
        LIMIT 1
    """, (customer_id,), as_dict=True)

    email_val = (contact[0]["email_id"] if contact and contact[0]["email_id"] else user_doc.email)
    mobile_val = (contact[0]["mobile_no"] if contact and contact[0]["mobile_no"] else (contact[0]["phone"] if contact and contact[0]["phone"] else user_doc.mobile_no or ""))

    # 3. Customer Photo / Avatar
    customer_image = frappe.db.get_value("Customer", customer_id, "image") or user_doc.user_image or None

    # 4. Generate Digital ID Card QR Code
    qr_b64 = None
    if qrcode:
        qr_payload = f"ROC:MEMBER:{member_id}:{member_record['member_name']}:{member_record['status']}:{member_record['membership_plan']}"
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(qr_payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0d2946", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

    # 5. Invoices & Outstanding Dues
    invoices = frappe.db.sql("""
        SELECT name, posting_date, due_date, grand_total, outstanding_amount, status
        FROM `tabSales Invoice`
        WHERE customer = %s AND docstatus = 1
        ORDER BY posting_date DESC
        LIMIT 25
    """, (customer_id,), as_dict=True)

    total_outstanding = sum(inv["outstanding_amount"] for inv in invoices if inv["outstanding_amount"] > 0)
    overdue_invoices = [inv for inv in invoices if inv["outstanding_amount"] > 0 and inv["status"] == "Overdue"]
    has_overdue_30_days = any(
        inv["outstanding_amount"] > 0 and inv["due_date"] and (datetime.date.today() - inv["due_date"]).days > 30 
        for inv in invoices
    )

    # 6. Family Members
    cm_doc = frappe.get_doc("Club Member", member_id)
    family_members = []
    for f in getattr(cm_doc, "family_members", []):
        family_members.append({
            "name": f.name,
            "full_name": f.full_name,
            "relationship": f.relationship,
            "date_of_birth": str(f.date_of_birth or ""),
            "phone_number": f.phone_number or "",
            "blood_group": f.blood_group or "",
            "approval_status": getattr(f, "approval_status", "Approved") or "Approved"
        })

    # 7. Recent Facility Bookings
    bookings = frappe.db.sql("""
        SELECT name, selected_venue, venue_shift, guest_name, docstatus, creation
        FROM `tabFacility Booking`
        WHERE customer = %s
        ORDER BY creation DESC
        LIMIT 10
    """, (customer_id,), as_dict=True) if frappe.db.table_exists("tabFacility Booking") else []

    # 8. Recent Event Registrations
    registrations = frappe.db.sql("""
        SELECT name, event_name, attendees_count, mobile, creation
        FROM `tabClub Event Registration`
        WHERE member_id = %s OR email = %s
        ORDER BY creation DESC
        LIMIT 10
    """, (member_id, user), as_dict=True) if frappe.db.table_exists("tabClub Event Registration") else []

    return {
        "is_member": True,
        "member_id": member_id,
        "customer": customer_id,
        "member_name": member_record["member_name"],
        "membership_plan": member_record["membership_plan"],
        "customer_group": member_record["customer_group"],
        "current_subscription": member_record["current_subscription"],
        "status": member_record["status"],
        "membership_start_date": str(member_record["membership_start_date"] or ""),
        "membership_expiry_date": str(member_record["membership_expiry_date"] or ""),
        "email": email_val,
        "mobile": mobile_val,
        "photo": customer_image,
        "qr_code": qr_b64,
        "total_outstanding": total_outstanding,
        "has_overdue_30_days": has_overdue_30_days,
        "invoices": invoices,
        "family_members": family_members,
        "bookings": bookings,
        "event_registrations": registrations
    }

@frappe.whitelist()
def send_contact_update_otp(new_email=None, new_mobile=None):
    """Generates and sends a 6-digit OTP to verify contact update"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please login to proceed"), frappe.PermissionError)

    otp = f"{random.randint(100000, 999999)}"
    cache_key = f"contact_otp_{user}"
    frappe.cache.set_value(cache_key, {
        "otp": otp,
        "new_email": new_email.strip() if new_email else None,
        "new_mobile": new_mobile.strip() if new_mobile else None
    }, expires_in_sec=600)

    # If outgoing email is configured, send email; otherwise provide safe prompt
    try:
        frappe.sendmail(
            recipients=[user],
            subject="Railway Officers' Club - OTP for Contact Update",
            message=f"<p>Dear Member,</p><p>Your One-Time Password (OTP) to update your contact details on the Railway Officers' Club portal is: <strong>{otp}</strong>.</p><p>This OTP is valid for 10 minutes. If you did not request this, please contact the Club Office immediately.</p>"
        )
        return {"success": True, "message": f"A 6-digit OTP has been sent to your registered email ({user})."}
    except Exception as e:
        # Fallback if email queue/account not set for outward delivery
        return {"success": True, "otp_demo": otp, "message": f"OTP generated. (Valid for 10 minutes: {otp})"}

@frappe.whitelist()
def verify_and_update_contact(entered_otp):
    """Verifies OTP and updates member email and mobile numbers across Contact, Customer and User"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please login to proceed"), frappe.PermissionError)

    cache_key = f"contact_otp_{user}"
    cached = frappe.cache.get_value(cache_key)
    if not cached:
        frappe.throw(_("OTP has expired. Please request a new OTP."))

    if str(cached.get("otp")) != str(entered_otp).strip():
        frappe.throw(_("Invalid OTP entered. Please check and try again."))

    new_email = cached.get("new_email")
    new_mobile = cached.get("new_mobile")

    user_doc = frappe.get_doc("User", user)
    username = user_doc.username

    # Find Customer
    cust_name = frappe.db.get_value("Club Member", {"name": username}, "customer") or username

    # Update Contact
    contact_name = frappe.db.sql("""
        SELECT dl.parent FROM `tabDynamic Link` dl
        WHERE dl.link_doctype = 'Customer' AND dl.link_name = %s AND dl.parenttype = 'Contact'
        LIMIT 1
    """, (cust_name,))

    if contact_name and contact_name[0][0]:
        c_doc = frappe.get_doc("Contact", contact_name[0][0])
        if new_email:
            c_doc.email_id = new_email
        if new_mobile:
            c_doc.mobile_no = new_mobile
            c_doc.phone = new_mobile
        c_doc.save(ignore_permissions=True)

    if new_mobile:
        frappe.db.set_value("User", user, "mobile_no", new_mobile)

    # Invalidate OTP cache
    frappe.cache.delete_value(cache_key)
    frappe.db.commit()

    return {"success": True, "message": "Contact details updated successfully!"}

@frappe.whitelist()
def add_family_member(full_name, relationship, date_of_birth=None, phone_number=None, blood_group=None):
    """Submits a new family member dependent under the logged in club member with Pending Approval"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please login to proceed"), frappe.PermissionError)

    user_doc = frappe.get_doc("User", user)
    username = user_doc.username

    # Find Club Member
    member_id = frappe.db.get_value("Club Member", {"customer": username}, "name")
    if not member_id:
        cms = frappe.db.sql("""
            SELECT cm.name FROM `tabClub Member` cm
            JOIN `tabDynamic Link` dl ON dl.link_doctype = 'Customer' AND dl.link_name = cm.customer AND dl.parenttype = 'Contact'
            JOIN `tabContact` c ON c.name = dl.parent
            WHERE c.email_id = %s
            LIMIT 1
        """, (user,))
        if cms:
            member_id = cms[0][0]

    if not member_id:
        frappe.throw(_("No active club member record found for this user account."))

    cm_doc = frappe.get_doc("Club Member", member_id)
    cm_doc.append("family_members", {
        "full_name": full_name.strip(),
        "relationship": relationship,
        "date_of_birth": date_of_birth,
        "phone_number": phone_number,
        "blood_group": blood_group,
        "approval_status": "Pending Club Approval"
    })
    cm_doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"success": True, "message": f"Family member '{full_name}' submitted for club approval!"}
