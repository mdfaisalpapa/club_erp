import frappe
from frappe import _
import io, base64, random, datetime, urllib.parse

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
    member_record = None
    if user_doc.username:
        member_record = frappe.db.get_value(
            "Club Member", 
            {"customer": user_doc.username}, 
            ["name", "customer", "member_name", "status", "membership_plan", "customer_group", "date_of_birth", "membership_start_date", "membership_expiry_date", "current_subscription"], 
            as_dict=True
        )

    if not member_record:
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
    unique_upi_id = f"roc{member_id}@fbl"

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

    # 4. Generate Digital ID Card Gate QR Code
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
        SELECT name, posting_date, due_date, grand_total, outstanding_amount, status,
               COALESCE(custom_reconciliation_status, 'Pending Payment') AS custom_reconciliation_status,
               custom_upi_utr
        FROM `tabSales Invoice`
        WHERE customer = %s AND docstatus = 1
        ORDER BY posting_date DESC
        LIMIT 30
    """, (customer_id,), as_dict=True)

    total_outstanding = sum(inv["outstanding_amount"] for inv in invoices if inv["outstanding_amount"] > 0)
    has_overdue_30_days = any(
        inv["outstanding_amount"] > 0 and inv["due_date"] and (datetime.date.today() - inv["due_date"]).days > 30 
        for inv in invoices
    )

    # 6. Family Members (with Dependent ID, Photo, and Gate QR)
    cm_doc = frappe.get_doc("Club Member", member_id)
    family_members = []
    for idx, f in enumerate(getattr(cm_doc, "family_members", []), 1):
        dep_id = getattr(f, "dependent_id", None) or f"{member_id}-F{idx}"
        approval = getattr(f, "approval_status", "Approved") or "Approved"
        photo_url = getattr(f, "photo", None) or ""
        
        # Generate QR code for family member
        fam_qr = None
        if qrcode:
            fam_qr_data = f"ROC:GATE:{dep_id}:{member_id}:{f.full_name}:{approval}"
            fam_qr_img = qrcode.make(fam_qr_data)
            fam_buf = io.BytesIO()
            fam_qr_img.save(fam_buf, format="PNG")
            fam_qr = "data:image/png;base64," + base64.b64encode(fam_buf.getvalue()).decode("utf-8")

        family_members.append({
            "name": f.name,
            "dependent_id": dep_id,
            "full_name": f.full_name,
            "relationship": f.relationship,
            "date_of_birth": str(f.date_of_birth or ""),
            "phone_number": f.phone_number or "",
            "blood_group": f.blood_group or "",
            "photo": photo_url,
            "qr_code": fam_qr,
            "approval_status": approval
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
        "unique_upi_id": unique_upi_id,
        "photo": customer_image,
        "qr_code": qr_b64,
        "total_outstanding": total_outstanding,
        "has_overdue_30_days": has_overdue_30_days,
        "invoices": invoices,
        "family_members": family_members,
        "bookings": bookings,
        "event_registrations": registrations
    }

@frappe.whitelist(allow_guest=True)
def get_invoice_upi_details(invoice_name):
    """Generates the dynamic UPI URI and QR code for an exact invoice and member VPA"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please login to proceed"), frappe.PermissionError)

    inv = frappe.get_doc("Sales Invoice", invoice_name)
    member_id = inv.customer
    outstanding = float(inv.outstanding_amount or inv.grand_total)

    vpa = f"roc{member_id}@fbl"
    clean_inv = invoice_name.replace("/", "-")
    payee_name = f"Railway Officers Club - {member_id}"

    # Generate standard NPCI UPI link
    # upi://pay?pa=...&pn=...&am=...&tn=...&cu=INR
    upi_uri = f"upi://pay?pa={vpa}&pn={urllib.parse.quote(payee_name)}&am={outstanding:.2f}&tn={clean_inv}&cu=INR"

    qr_base64 = None
    if qrcode:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(upi_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0a1f35", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_base64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

    return {
        "invoice_name": invoice_name,
        "member_id": member_id,
        "vpa": vpa,
        "outstanding_amount": outstanding,
        "upi_uri": upi_uri,
        "qr_code": qr_base64,
        "reconciliation_status": inv.custom_reconciliation_status or "Pending Payment",
        "utr_ref": inv.custom_upi_utr or ""
    }

@frappe.whitelist(allow_guest=True)
def mark_invoice_under_reconciliation(invoice_name, utr_no=None):
    """Marks an invoice as 'Under Reconciliation' when the member transfers funds via UPI"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please login to proceed"), frappe.PermissionError)

    inv = frappe.get_doc("Sales Invoice", invoice_name)
    inv.custom_reconciliation_status = "Under Reconciliation"
    if utr_no:
        inv.custom_upi_utr = utr_no.strip()
    inv.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "success": True, 
        "message": f"Payment submitted for Invoice {invoice_name}. Status marked as 'Under Reconciliation'. Once matched with the Federal Bank statement, it will be marked as Reconciled."
    }

@frappe.whitelist(allow_guest=True)
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

    try:
        frappe.sendmail(
            recipients=[user],
            subject="Railway Officers' Club - OTP for Contact Update",
            message=f"<p>Dear Member,</p><p>Your One-Time Password (OTP) to update your contact details on the Railway Officers' Club portal is: <strong>{otp}</strong>.</p><p>This OTP is valid for 10 minutes. If you did not request this, please contact the Club Office immediately.</p>"
        )
        return {"success": True, "message": f"A 6-digit OTP has been sent to your registered email ({user})."}
    except Exception as e:
        return {"success": True, "otp_demo": otp, "message": f"OTP generated. (Valid for 10 minutes: {otp})"}

@frappe.whitelist(allow_guest=True)
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

    cust_name = frappe.db.get_value("Club Member", {"name": username}, "customer") or username

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

    frappe.cache.delete_value(cache_key)
    frappe.db.commit()

    return {"success": True, "message": "Contact details updated successfully!"}

@frappe.whitelist(allow_guest=True)
def add_family_member(full_name, relationship, date_of_birth=None, phone_number=None, blood_group=None, photo_base64=None):
    """Submits a new family member dependent under the logged in club member with photo and auto-generated Dependent ID"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please login to proceed"), frappe.PermissionError)

    user_doc = frappe.get_doc("User", user)
    username = user_doc.username

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
    current_count = len(getattr(cm_doc, "family_members", [])) + 1
    dep_id = f"{member_id}-F{current_count}"

    photo_url = None
    if photo_base64 and "," in photo_base64:
        from frappe.utils.file_manager import save_file
        header, encoded = photo_base64.split(",", 1)
        ext = "png"
        if "jpeg" in header or "jpg" in header:
            ext = "jpg"
        file_data = base64.b64decode(encoded)
        filename = f"fam_{dep_id}_{int(datetime.datetime.now().timestamp())}.{ext}"
        saved_file = save_file(filename, file_data, "Club Member", cm_doc.name, is_private=0)
        photo_url = saved_file.file_url

    cm_doc.append("family_members", {
        "full_name": full_name.strip(),
        "dependent_id": dep_id,
        "photo": photo_url,
        "relationship": relationship,
        "date_of_birth": date_of_birth,
        "phone_number": phone_number,
        "blood_group": blood_group,
        "approval_status": "Pending Club Approval"
    })
    cm_doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"success": True, "message": f"Family member '{full_name}' ({dep_id}) submitted for club approval!"}


@frappe.whitelist(allow_guest=True)
def get_total_outstanding_upi_details():
    """Generates the dynamic UPI URI and QR code for the member's total outstanding balance"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please login to proceed"), frappe.PermissionError)

    user_doc = frappe.get_doc("User", user)
    username = user_doc.username

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
        frappe.throw(_("No active club member record found."))

    customer_id = frappe.db.get_value("Club Member", member_id, "customer") or member_id

    # Fetch all open invoices
    invoices = frappe.db.sql("""
        SELECT name, outstanding_amount, custom_reconciliation_status, custom_upi_utr
        FROM `tabSales Invoice`
        WHERE customer = %s AND docstatus = 1 AND outstanding_amount > 0
        ORDER BY posting_date ASC
    """, (customer_id,), as_dict=True)

    total_outstanding = sum(float(inv["outstanding_amount"]) for inv in invoices)
    if total_outstanding <= 0:
        return {
            "has_dues": False,
            "message": "All club invoices and dues are fully settled. No payment required."
        }

    vpa = f"roc{member_id}@fbl"
    payee_name = f"Railway Officers Club - {member_id}"
    inv_names_summary = ", ".join(inv["name"] for inv in invoices)
    clean_note = f"DUES-MEMBER-{member_id}"

    upi_uri = f"upi://pay?pa={vpa}&pn={urllib.parse.quote(payee_name)}&am={total_outstanding:.2f}&tn={clean_note}&cu=INR"

    qr_base64 = None
    if qrcode:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(upi_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0a1f35", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_base64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

    all_under_reconcile = all(inv.get("custom_reconciliation_status") == "Under Reconciliation" for inv in invoices)

    return {
        "has_dues": True,
        "member_id": member_id,
        "vpa": vpa,
        "total_outstanding": total_outstanding,
        "open_invoices_count": len(invoices),
        "invoice_names": [inv["name"] for inv in invoices],
        "upi_uri": upi_uri,
        "qr_code": qr_base64,
        "is_under_reconciliation": all_under_reconcile
    }

@frappe.whitelist(allow_guest=True)
def mark_all_under_reconciliation(utr_no=None):
    """Marks all pending open invoices under reconciliation for this member"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please login to proceed"), frappe.PermissionError)

    user_doc = frappe.get_doc("User", user)
    username = user_doc.username

    customer_id = frappe.db.get_value("Club Member", {"customer": username}, "customer") or username

    invoices = frappe.db.sql("""
        SELECT name FROM `tabSales Invoice`
        WHERE customer = %s AND docstatus = 1 AND outstanding_amount > 0
    """, (customer_id,), as_dict=True)

    for inv in invoices:
        frappe.db.set_value("Sales Invoice", inv["name"], {
            "custom_reconciliation_status": "Under Reconciliation",
            "custom_upi_utr": utr_no.strip() if utr_no else None
        })

    frappe.db.commit()
    return {
        "success": True,
        "message": f"All {len(invoices)} pending invoices marked as 'Under Reconciliation'. Once matched with the Federal Bank statement, they will be marked as Reconciled."
    }


@frappe.whitelist(allow_guest=True)
def get_invoice_details(invoice_name):
    """Returns line items, taxes, totals, and print format link for a specific invoice"""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please login to proceed"), frappe.PermissionError)

    inv = frappe.get_doc("Sales Invoice", invoice_name)
    
    items = []
    for it in inv.items:
        items.append({
            "item_code": it.item_code,
            "item_name": it.item_name,
            "description": it.description or "",
            "qty": it.qty,
            "rate": it.rate,
            "amount": it.amount
        })

    taxes = []
    for tx in getattr(inv, "taxes", []):
        taxes.append({
            "account_head": tx.account_head,
            "tax_amount": tx.tax_amount
        })

    pdf_url = f"/api/method/club_erp.api.member_portal.download_member_invoice_pdf?invoice_name={urllib.parse.quote(inv.name)}"

    return {
        "invoice_name": inv.name,
        "customer": inv.customer,
        "customer_name": inv.customer_name,
        "posting_date": str(inv.posting_date or ""),
        "due_date": str(inv.due_date or ""),
        "net_total": inv.net_total,
        "total_taxes_and_charges": getattr(inv, "total_taxes_and_charges", 0.0),
        "grand_total": inv.grand_total,
        "outstanding_amount": inv.outstanding_amount,
        "status": inv.status,
        "reconciliation_status": inv.custom_reconciliation_status or "Pending Payment",
        "utr_ref": inv.custom_upi_utr or "",
        "items": items,
        "taxes": taxes,
        "pdf_url": pdf_url
    }


@frappe.whitelist(allow_guest=True)
def download_member_invoice_pdf(invoice_name):
    """Securely downloads PDF for a member's sales invoice"""
    user = frappe.session.user
    if user == "Guest":
        frappe.local.response.http_status_code = 401
        frappe.throw(_("Please login to download invoices"), frappe.PermissionError)

    if not invoice_name:
        frappe.throw(_("Invoice name is required"), frappe.ValidationError)

    inv = frappe.get_doc("Sales Invoice", invoice_name)
    user_doc = frappe.get_doc("User", user)

    # Permission verification: user must be Administrator, System Manager,
    # or the owner/customer of the invoice
    is_authorized = False
    if "System Manager" in frappe.get_roles(user) or user == "Administrator":
        is_authorized = True
    elif user_doc.username and user_doc.username == inv.customer:
        is_authorized = True
    else:
        # Check if customer's contact has this email
        linked_customer = frappe.db.sql("""
            SELECT dl.link_name FROM `tabDynamic Link` dl
            JOIN `tabContact` c ON c.name = dl.parent
            WHERE dl.link_doctype = 'Customer' AND dl.link_name = %s AND c.email_id = %s
            LIMIT 1
        """, (inv.customer, user))
        if linked_customer:
            is_authorized = True
        else:
            # Check portal users table
            is_pu = frappe.db.get_value("Portal User", {"parent": inv.customer, "user": user})
            if is_pu:
                is_authorized = True

    if not is_authorized:
        frappe.local.response.http_status_code = 403
        frappe.throw(_("You are not authorized to download this invoice"), frappe.PermissionError)

    # Generate PDF
    # Switch to Administrator or ignore_permissions to guarantee clean generation
    original_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        pdf_bytes = frappe.get_print("Sales Invoice", inv.name, "Single Page Member Invoice", as_pdf=True, no_letterhead=1)
    finally:
        frappe.set_user(original_user)

    filename = f"Invoice_{inv.name.replace('/', '_')}.pdf"
    frappe.local.response.filename = filename
    frappe.local.response.filecontent = pdf_bytes
    frappe.local.response.type = "pdf"


def handle_post_login(login_manager):
    """Redirects Website Users directly to /member-portal upon login"""
    user = login_manager.user
    user_type = frappe.db.get_value("User", user, "user_type")
    if user_type == "Website User":
        redirect_target = "/member-portal"
        frappe.local.response["home_page"] = redirect_target
        frappe.local.response["redirect_to"] = redirect_target
        frappe.cache.hset("redirect_after_login", user, redirect_target)
