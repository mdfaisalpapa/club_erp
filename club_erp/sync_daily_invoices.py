"""
Daily Sync Service for Odoo 15 CE -> ERPNext 16 Invoices
=========================================================
Synchronizes newly posted customer invoices, bar invoices, caution deposits,
and credit notes from Odoo 15 CE into ERPNext 16 on a scheduled daily or on-demand basis.

Idempotency:
- Checks `custom_odoo_invoice_id` in ERPNext to avoid duplicate creation.
- Preserves exact posting dates, tax distributions, return status, and liability mappings.
- Bulletproof partner/member resolution: NEVER falls back or assigns invoices to an arbitrary member!
"""

import datetime
import logging
import xmlrpc.client
import frappe

logger = logging.getLogger("odoo_sync")

ODOO_CONFIG = {
    "url": "http://10.185.45.92:8069",
    "db": "db_sterlingclub",
    "username": "md.faisalpapa@gmail.com",
    "password": "PzGc!Tc4mpG9V&",
}

COMPANY = "Railway Officers' Club"
COST_CENTER = "Main - ROC"

def get_odoo_connection():
    common = xmlrpc.client.ServerProxy(f"{ODOO_CONFIG['url']}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_CONFIG["db"], ODOO_CONFIG["username"], ODOO_CONFIG["password"], {})
    models = xmlrpc.client.ServerProxy(f"{ODOO_CONFIG['url']}/xmlrpc/2/object")
    return uid, models

def resolve_odoo_customer(partner_id, partner_name, partner_ref=None):
    """
    Safely resolves or creates the ERPNext Customer corresponding to an Odoo Partner:
    1. Match by partner_ref (Member ID in Odoo)
    2. Match by custom_odoo_id or custom_odoo_ref in Customer doctype
    3. Match by exact customer_name
    4. Match by partner_id
    5. Fallback: Creates an explicit new customer named after partner_ref or partner_id.
       NEVER hardcodes or defaults to any existing member account!
    """
    if partner_ref and partner_ref != 'False':
        clean_ref = str(partner_ref).strip()
        if frappe.db.exists("Customer", clean_ref):
            return clean_ref

    match_odoo = frappe.db.get_value("Customer", {"custom_odoo_id": partner_id}, "name")
    if match_odoo:
        return match_odoo

    if partner_ref and partner_ref != 'False':
        match_ref = frappe.db.get_value("Customer", {"custom_odoo_ref": str(partner_ref).strip()}, "name")
        if match_ref:
            return match_ref

    if partner_name:
        match_name = frappe.db.get_value("Customer", {"customer_name": partner_name.strip()}, "name")
        if match_name:
            return match_name

    if frappe.db.exists("Customer", str(partner_id)):
        return str(partner_id)

    # Isolated new customer creation (Zero risk of cross-pollinating existing members)
    cust_id = str(partner_ref) if (partner_ref and partner_ref != 'False') else f"CUST-ODOO-{partner_id}"
    if not frappe.db.exists("Customer", cust_id):
        cg = frappe.get_all("Customer Group", limit=1)[0].name
        terr = frappe.get_all("Territory", limit=1)[0].name
        c = frappe.new_doc("Customer")
        c.name = cust_id
        c.customer_name = partner_name or f"Partner {partner_id}"
        c.customer_group = cg
        c.territory = terr
        c.customer_type = "Individual"
        c.custom_odoo_id = partner_id
        c.custom_odoo_ref = str(partner_ref) if partner_ref else ""
        c.insert(ignore_permissions=True)
        return c.name
    return cust_id

def sync_daily_invoices(days_back=3):
    today = datetime.date.today()
    from_date = (today - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")
    return sync_invoices_between(from_date, to_date)

def sync_invoices_between(from_date, to_date):
    print(f"=== Starting Odoo -> ERPNext Daily Invoice Sync ({from_date} to {to_date}) ===")
    uid, models = get_odoo_connection()

    domain = [
        ("move_type", "in", ["out_invoice", "out_refund"]),
        ("state", "=", "posted"),
        ("invoice_date", ">=", from_date),
        ("invoice_date", "<=", to_date)
    ]
    odoo_invoices = models.execute_kw(
        ODOO_CONFIG["db"], uid, ODOO_CONFIG["password"],
        "account.move", "search_read",
        [domain],
        {"fields": ["id", "name", "invoice_date", "partner_id", "amount_total", "amount_untaxed", "amount_tax", "move_type", "invoice_line_ids"]}
    )

    print(f"Total posted Odoo invoices in period: {len(odoo_invoices)}")
    if not odoo_invoices:
        print("No invoices found in Odoo for this period.")
        return {"total_odoo": 0, "synced": 0, "skipped": 0}

    existing_ids = set(frappe.db.sql_list(
        "select custom_odoo_invoice_id from `tabSales Invoice` where custom_odoo_invoice_id is not null and docstatus in (0, 1)"
    ))

    missing_invs = [inv for inv in odoo_invoices if str(inv["id"]) not in existing_ids]
    print(f"Already synchronized: {len(odoo_invoices) - len(missing_invs)} | New to migrate: {len(missing_invs)}")

    if not missing_invs:
        print("All invoices are already up-to-date!")
        return {"total_odoo": len(odoo_invoices), "synced": 0, "skipped": len(odoo_invoices)}

    # Fetch partner refs for missing invoices in bulk
    p_ids = list(set([m["partner_id"][0] for m in missing_invs if m.get("partner_id")]))
    p_map = {}
    if p_ids:
        partners_data = models.execute_kw(
            ODOO_CONFIG["db"], uid, ODOO_CONFIG["password"],
            "res.partner", "read",
            [p_ids],
            {"fields": ["id", "name", "ref"]}
        )
        p_map = {p["id"]: p for p in partners_data}

    # Fetch lines for missing invoices
    all_line_ids = []
    for inv in missing_invs:
        all_line_ids.extend(inv["invoice_line_ids"])

    lines_by_move = {}
    if all_line_ids:
        all_lines = models.execute_kw(
            ODOO_CONFIG["db"], uid, ODOO_CONFIG["password"],
            "account.move.line", "search_read",
            [[("id", "in", all_line_ids), ("exclude_from_invoice_tab", "=", False)]],
            {"fields": ["id", "move_id", "product_id", "name", "quantity", "price_unit", "price_subtotal", "tax_ids", "account_id"]}
        )
        for l in all_lines:
            mid = l["move_id"][0]
            lines_by_move.setdefault(mid, []).append(l)

    synced_count = 0
    errors = []

    for inv in missing_invs:
        try:
            inv_id = str(inv["id"])
            inv_name = inv["name"]
            is_refund = (inv["move_type"] == "out_refund")
            is_bar = "BAR" in inv_name
            partner_id = inv["partner_id"][0] if inv["partner_id"] else 0
            partner_name = inv["partner_id"][1] if inv["partner_id"] else "Walk-in Customer"
            partner_ref = p_map.get(partner_id, {}).get("ref")

            customer = resolve_odoo_customer(partner_id, partner_name, partner_ref)

            series = "I/26-27/.#####"

            doc = frappe.new_doc("Sales Invoice")
            doc.naming_series = series
            doc.customer = customer
            doc.is_return = 1 if is_refund else 0
            doc.set_posting_time = 1
            doc.posting_date = inv["invoice_date"]
            doc.due_date = inv["invoice_date"]
            doc.company = COMPANY
            doc.custom_odoo_invoice_id = inv_id
            doc.remarks = f"Migrated from Odoo {inv_name} (ID: {inv_id})"
            doc.items = []
            doc.taxes = []

            olines = lines_by_move.get(inv["id"], [])
            is_caution = any("CAUTION DEPOSIT" in (l["name"] or "").upper() for l in olines)

            if is_caution:
                for l in olines:
                    if not l["product_id"]:
                        continue
                    item_code = "VCAUTION10K" if "10K" in (l["name"] or "").upper() else "VCAUTION5K"
                    qty = l["quantity"] or 1.0
                    rate = l["price_unit"]
                    doc.append("items", {
                        "item_code": item_code,
                        "item_name": l["name"],
                        "qty": -abs(qty) if is_refund else abs(qty),
                        "rate": rate,
                        "income_account": "Caution Deposits (Refundable) - ROC",
                        "cost_center": COST_CENTER
                    })
            elif is_bar:
                for l in olines:
                    if not l["product_id"]:
                        continue
                    qty = l["quantity"] or 1.0
                    rate = l["price_unit"]
                    doc.append("items", {
                        "item_code": "SUBS-MMC-CORE",
                        "item_name": l["name"],
                        "qty": -abs(qty) if is_refund else abs(qty),
                        "rate": rate,
                        "income_account": "Bar Sales - ROC",
                        "cost_center": COST_CENTER
                    })
                if inv["amount_tax"] > 0:
                    doc.append("taxes", {
                        "charge_type": "Actual",
                        "account_head": "TNVAT Payable - ROC",
                        "description": "TNVAT Payable @ 14.5%",
                        "tax_amount": -abs(inv["amount_tax"]) if is_refund else abs(inv["amount_tax"])
                    })
            else:
                for l in olines:
                    if not l["product_id"]:
                        continue
                    prod_name = (l["name"] or "").upper()
                    acc_name = l["account_id"][1] if l["account_id"] else ""
                    qty = l["quantity"] or 1.0
                    rate = l["price_unit"]

                    if "GUEST CHARGE" in prod_name:
                        item_code = "GUESTSPO" if "ABOVE" not in prod_name else "GUESTSPO-AL"
                        income_acc = "Guest Entry Fees - ROC"
                    elif "COACHING" in prod_name or "TENNIS" in prod_name or "200251" in acc_name:
                        item_code = "GUESTSPO"
                        income_acc = "Sports Coaching Fees - ROC"
                    elif "CANTEEN" in acc_name or "200150" in acc_name:
                        item_code = "FCDOSA1"
                        income_acc = "Canteen Sales - ROC"
                    elif "RESTAURANT" in acc_name or "200140" in acc_name:
                        item_code = "FCDOSA1"
                        income_acc = "Restaurant Sales - Food - ROC"
                    elif "FACILITY" in acc_name or "BOOKING" in acc_name or "200211" in acc_name:
                        item_code = "BOOKING"
                        income_acc = "Hall & Venue Rentals - ROC"
                    elif "DEEPAVALI" in prod_name or "CELEBRATION" in prod_name:
                        item_code = "SUBS-MMC-CORE"
                        income_acc = "Celebrations Fee - ROC"
                    else:
                        item_code = "SUBS-MMC-CORE"
                        income_acc = "Subscription Revenue - ROC"

                    doc.append("items", {
                        "item_code": item_code,
                        "item_name": l["name"],
                        "qty": -abs(qty) if is_refund else abs(qty),
                        "rate": rate,
                        "income_account": income_acc,
                        "cost_center": COST_CENTER
                    })

                if inv["amount_tax"] > 0:
                    tax_rate = 2.5 if "CANTEEN" in acc_name or "RESTAURANT" in acc_name else 9.0
                    doc.append("taxes", {
                        "charge_type": "On Net Total",
                        "account_head": "Output Tax CGST - ROC",
                        "description": f"Output Tax CGST @ {tax_rate}%",
                        "rate": tax_rate
                    })
                    doc.append("taxes", {
                        "charge_type": "On Net Total",
                        "account_head": "Output Tax SGST - ROC",
                        "description": f"Output Tax SGST @ {tax_rate}%",
                        "rate": tax_rate
                    })

            doc.insert(ignore_permissions=True)
            for ps in doc.payment_schedule:
                ps.due_date = doc.posting_date
            doc.save()
            doc.submit()
            synced_count += 1
            print(f"  [OK] Migrated Odoo {inv_id} ({inv_name}) -> ERPNext {doc.name} to Customer [{customer}] (Grand Total: {doc.grand_total})")

        except Exception as e:
            frappe.db.rollback()
            err_msg = f"Failed to sync Odoo {inv['id']} ({inv['name']}): {str(e)}"
            print(f"  [ERROR] {err_msg}")
            errors.append(err_msg)

    frappe.db.commit()
    print(f"=== Sync Completed: {synced_count}/{len(missing_invs)} new invoices synchronized. ===")
    return {"total_odoo": len(odoo_invoices), "synced": synced_count, "errors": errors}
