import frappe
from frappe.model.document import Document
from frappe.utils import today, date_diff
from frappe.model.naming import make_autoname


class ClubMember(Document):
    
    def validate(self):
        # Trigger the validation check before any save happens
        self.validate_active_subscription()
        
    def before_save(self):
        self.sync_subscription_status()
        
    def validate_active_subscription(self):
        """
        Blocks manual activation if the member does not have a live subscription.
        """
        if self.status == "Active":
            # Check if there is at least one live subscription
            has_subscription = frappe.db.exists(
                "Subscription",
                {
                    "party_type": "Customer",
                    "party": self.name,
                    "status": ["in", ["Active", "Unpaid", "Past Due Date"]]
                }
            )
            
            if not has_subscription:
                frappe.throw(
                    "<b>Activation Blocked:</b> You must create and submit a Subscription for this member before setting their status to Active."
                )

            
    def autoname(self):
        """
        Generate the Club Member ID using the naming series configured
        on the selected Customer Group.
        """

        if self.customer_group and not self.naming_series:
            series = frappe.db.get_value(
                "Customer Group",
                self.customer_group,
                "custom_naming_series",
            )

            if series:
                self.naming_series = series

        if self.naming_series:
            self.name = make_autoname(self.naming_series)    
    def sync_subscription_status(self):
        """
        Silently updates the Data field with readable Plan Names for the List View
        """
        # --- THE FIX: Instantly clear the field if the member is being deactivated ---
        if self.status in ["Inactive", "Suspended"]:
            self.current_subscription = "No Active Plan"
            return
        # -----------------------------------------------------------------------------

        active_sub = frappe.get_all(
            "Subscription",
            filters={
                "party_type": "Customer",
                "party": self.name,
                "status": ["in", ["Active", "Unpaid", "Past Due Date"]]
            },
            fields=["name"],
            limit=1
        )
        
        if not active_sub:
            self.current_subscription = "No Active Plan"
            return
            
        try:
            sub_doc = frappe.get_doc("Subscription", active_sub[0].name)
            plans = [p.plan for p in sub_doc.plans]
            self.current_subscription = ", ".join(plans)
        except Exception:
            self.current_subscription = "Error Loading Plan"
    # ------------------------------------------------------------------
    # VALIDATION
    # ------------------------------------------------------------------

    def validate(self):
        self.validate_membership_plan()
        self.validate_nominee_quota()

    def validate_membership_plan(self):
        """
        Validate the selected Club Membership Plan and ensure that it
        belongs to the selected Customer Group.
        """

        # Membership Plan is required for actual members.
        member_groups = [
            "Institutional Member",
            "Institutional Nominee",
            "Other Government Officer",
            "Private Member",
            "Railway Life Member",
            "Railway Non Life Member",
            "Railway Ward Member",
        ]

        if self.customer_group not in member_groups:
            return

        if not self.membership_plan:
            frappe.throw(
                "Membership Plan is mandatory for Customer Group "
                f"<b>{self.customer_group}</b>."
            )

        plan = frappe.db.get_value(
            "Club Membership Plan",
            self.membership_plan,
            [
                "enabled",
                "membership_item",
                "customer_group",
                "validity_in_years",
                "is_institutional",
                "allowed_nominees",
                "membership_rate",
            ],
            as_dict=True,
        )

        if not plan:
            frappe.throw(
                f"Membership Plan <b>{self.membership_plan}</b> does not exist."
            )

        if not plan.enabled:
            frappe.throw(
                f"Membership Plan <b>{self.membership_plan}</b> is disabled."
            )

        if not plan.membership_item:
            frappe.throw(
                f"Membership Plan <b>{self.membership_plan}</b> "
                "does not have a Membership Item."
            )

        if plan.customer_group != self.customer_group:
            frappe.throw(
                f"Membership Plan <b>{self.membership_plan}</b> belongs to "
                f"Customer Group <b>{plan.customer_group}</b>, but this "
                f"Club Member belongs to <b>{self.customer_group}</b>."
            )

        # Institutional Member must use an institutional plan.
        if self.customer_group == "Institutional Member":
            if not plan.is_institutional:
                frappe.throw(
                    f"Membership Plan <b>{self.membership_plan}</b> "
                    "is not an institutional membership plan."
                )

            if not plan.allowed_nominees or plan.allowed_nominees <= 0:
                frappe.throw(
                    f"Membership Plan <b>{self.membership_plan}</b> "
                    "must specify the number of allowed nominees."
                )

        # Institutional Nominees must use an institutional plan.
        elif self.customer_group == "Institutional Nominee":
            if not plan.is_institutional:
                frappe.throw(
                    f"Membership Plan <b>{self.membership_plan}</b> "
                    "is not an institutional membership plan."
                )

        # Non-institutional members should not carry an institutional
        # nominee allowance on the Club Member record, if such a field
        # exists on the DocType.
        if not plan.is_institutional and hasattr(self, "allowed_nominees"):
            self.allowed_nominees = 0

    def validate_nominee_quota(self):
        """
        Server-side protection against exceeding the nominee quota
        configured on the parent institution's Membership Plan.
        """

        if self.customer_group != "Institutional Nominee":
            return

        if not self.parent_institution:
            return

        parent_plan = frappe.db.get_value(
            "Club Member",
            self.parent_institution,
            "membership_plan",
        )

        if not parent_plan:
            frappe.throw(
                "The selected Parent Institution does not have a "
                "Membership Plan."
            )

        max_limit = frappe.db.get_value(
            "Club Membership Plan",
            parent_plan,
            "allowed_nominees",
        ) or 0

        max_limit = int(max_limit)

        if max_limit <= 0:
            frappe.throw(
                f"Membership Plan <b>{parent_plan}</b> does not permit "
                "any nominees."
            )

        filters = {
            "parent_institution": self.parent_institution,
            "status": ["!=", "Inactive"],
        }

        # Do not count the current nominee when editing an existing record.
        if not self.is_new():
            filters["name"] = ["!=", self.name]

        active_count = frappe.db.count(
            "Club Member",
            filters=filters,
        )

        if active_count >= max_limit:
            frappe.throw(
                f"Institution quota full. Maximum <b>{max_limit}</b> "
                f"active nominees are allowed for Membership Plan "
                f"<b>{parent_plan}</b>."
            )

    # ------------------------------------------------------------------
    # CUSTOMER CREATION
    # ------------------------------------------------------------------

    def before_insert(self):

        # Migration records must not create Customers or trigger
        # membership automation.
        if getattr(self.flags, "is_migration", False):
            return

        # Games Membership is no longer offered.
        if self.customer_group == "Games Member":
            frappe.throw(
                "<b>Enrollment Closed:</b> The 'Games Member' membership "
                "is not currently being offered to new members."
            )

        # Auto-create the ERPNext Customer ledger.
        if not self.customer:
            new_customer = frappe.new_doc("Customer")
            new_customer.customer_name = self.member_name
            new_customer.customer_group = self.customer_group
            new_customer.insert(ignore_permissions=True)

            self.customer = new_customer.name

    # ------------------------------------------------------------------
    # POST CREATION
    # ------------------------------------------------------------------

    def after_insert(self):

        if getattr(self.flags, "is_migration", False):
            return

        if not self.membership_plan:
            return

        self.generate_joining_invoice()
        self.generate_recurring_subscription()

    # ------------------------------------------------------------------
    # JOINING FEE
    # ------------------------------------------------------------------

    def generate_joining_invoice(self):
        """
        Generate the joining-fee Sales Invoice using the Item linked
        to the selected Club Membership Plan.
        """

        if not self.membership_plan:
            return

        plan = frappe.db.get_value(
            "Club Membership Plan",
            self.membership_plan,
            [
                "enabled",
                "membership_item",
                "membership_rate",
            ],
            as_dict=True,
        )

        if not plan:
            frappe.throw(
                f"Membership Plan <b>{self.membership_plan}</b> "
                "does not exist."
            )

        if not plan.enabled:
            frappe.throw(
                f"Membership Plan <b>{self.membership_plan}</b> is disabled."
            )

        if not plan.membership_item:
            frappe.throw(
                f"Membership Plan <b>{self.membership_plan}</b> "
                "does not have a Membership Item."
            )

        si = frappe.new_doc("Sales Invoice")

        si.customer = self.customer
        si.set_posting_time = 1

        si.append(
            "items",
            {
                "item_code": plan.membership_item,
                "qty": 1,
            },
        )

        si.set_missing_values()
        si.calculate_taxes_and_totals()
        si.insert(ignore_permissions=True)

        frappe.msgprint(
            (
                "Joining Fee Invoice Generated: "
                "<a href='/app/sales-invoice/{0}'>"
                "<b>{0}</b></a>"
            ).format(si.name),
            indicator="green",
        )

    # ------------------------------------------------------------------
    # RECURRING CGF SUBSCRIPTION
    # ------------------------------------------------------------------

    def generate_recurring_subscription(self):

        age = 0

        if self.date_of_birth:
            age = int(
                date_diff(today(), self.date_of_birth) / 365.25
            )

        cgf_plan_name = None

        non_railway_groups = [
            "Institutional Member",
            "Institutional Nominee",
            "Other Government Officer",
            "Private Member",
            "Railway Ward Member",
        ]

        if self.customer_group in non_railway_groups:

            if age >= 60:
                cgf_plan_name = "CGF - Non Railway Member 60+"
            else:
                cgf_plan_name = "CGF - Non Railway Member"

        elif self.customer_group == "Railway Life Member":

            if age >= 80:
                cgf_plan_name = "CGF - Railway Sr Citizen 80+"

            elif age >= 65:
                cgf_plan_name = "CGF - Railway Sr Citizen 65+"

            else:
                cgf_plan_name = "CGF - Railway Life Member"

        elif self.customer_group == "Railway Non Life Member":

            cgf_plan_name = "CGF - Railway Non Life Member"

        if not cgf_plan_name:
            return

        # Prevent duplicate active subscriptions for the same Customer.
        existing_active = frappe.db.exists(
            "Subscription",
            {
                "party_type": "Customer",
                "party": self.customer,
                "status": "Active",
            },
        )

        if existing_active:
            frappe.throw(
                f"Customer <b>{self.customer}</b> already has an "
                "Active Subscription."
            )

        sub = frappe.new_doc("Subscription")

        sub.party_type = "Customer"
        sub.party = self.customer
        sub.start_date = self.membership_start_date or today()
        sub.generate_invoice = 1

        sub.append(
            "plans",
            {
                "plan": cgf_plan_name,
                "qty": 1,
            },
        )

        sub.insert(ignore_permissions=True)

        frappe.msgprint(
            (
                "Recurring Subscription Generated: "
                "<a href='/app/subscription/{0}'>"
                "<b>{0}</b></a>"
            ).format(sub.name),
            indicator="green",
        )

    # ------------------------------------------------------------------
    # STATUS / CUSTOMER / SUBSCRIPTION SYNCHRONISATION
    # ------------------------------------------------------------------

    def on_update(self):

        if getattr(self.flags, "is_migration", False):
            return

        if not self.has_value_changed("status"):
            return

        # Customer disabled state follows Club Member status.
        is_disabled = (
            1
            if self.status in ["Inactive", "Suspended"]
            else 0
        )

        if self.customer:
            frappe.db.set_value(
                "Customer",
                self.customer,
                "disabled",
                is_disabled,
            )

        FROZEN_PLAN_NAME = "Frozen - Private Member"

        existing_subs = frappe.get_all(
            "Subscription",
            filters={
                "party_type": "Customer",
                "party": self.customer,
            },
            fields=["name", "status"],
        )

        has_frozen = False

        for sub_data in existing_subs:

            sub_doc = frappe.get_doc(
                "Subscription",
                sub_data.name,
            )

            is_frozen_sub = any(
                p.plan == FROZEN_PLAN_NAME
                for p in sub_doc.plans
            )

            if is_frozen_sub:
                has_frozen = True

            # Suspended / Inactive members
            if (
                self.status in ["Inactive", "Suspended"]
                and sub_doc.status == "Active"
            ):
                frappe.db.set_value(
                    "Subscription",
                    sub_doc.name,
                    "status",
                    "Paused",
                )

            # Frozen member
            elif self.status == "Frozen":

                if (
                    not is_frozen_sub
                    and sub_doc.status == "Active"
                ):
                    frappe.db.set_value(
                        "Subscription",
                        sub_doc.name,
                        "status",
                        "Paused",
                    )

                elif (
                    is_frozen_sub
                    and sub_doc.status == "Paused"
                ):
                    frappe.db.set_value(
                        "Subscription",
                        sub_doc.name,
                        "status",
                        "Active",
                    )

            # Active member
            elif self.status == "Active":

                if (
                    is_frozen_sub
                    and sub_doc.status == "Active"
                ):
                    frappe.db.set_value(
                        "Subscription",
                        sub_doc.name,
                        "status",
                        "Paused",
                    )

                elif (
                    not is_frozen_sub
                    and sub_doc.status == "Paused"
                ):
                    frappe.db.set_value(
                        "Subscription",
                        sub_doc.name,
                        "status",
                        "Active",
                    )

        # Create Frozen Subscription if required.
        if self.status == "Frozen" and not has_frozen:

            new_frozen_sub = frappe.new_doc(
                "Subscription"
            )

            new_frozen_sub.party_type = "Customer"
            new_frozen_sub.party = self.customer
            new_frozen_sub.start_date = today()
            new_frozen_sub.generate_invoice = 1

            new_frozen_sub.append(
                "plans",
                {
                    "plan": FROZEN_PLAN_NAME,
                    "qty": 1,
                },
            )

            new_frozen_sub.insert(
                ignore_permissions=True
            )

            frappe.msgprint(
                (
                    "Generated new Frozen Subscription: "
                    "<b>{0}</b>"
                ).format(new_frozen_sub.name),
                indicator="green",
            )


# ----------------------------------------------------------------------
# MEMBERSHIP NUMBER PREVIEW
# ----------------------------------------------------------------------
from frappe.utils import getdate, nowdate

@frappe.whitelist()
def get_inactive_subscriptions(party_name):
    """
    Fetches a list of paused, unpaid, past due, or cancelled subscriptions 
    belonging to the customer along with their readable plan names.
    """
    subs = frappe.get_all(
        "Subscription",
        filters={
            "party_type": "Customer",
            "party": party_name,
            "status": ["in", ["Paused", "Unpaid", "Past Due Date", "Cancelled"]]
        },
        fields=["name", "status", "start_date", "end_date"]
    )
    
    result = []
    for s in subs:
        try:
            sub_doc = frappe.get_doc("Subscription", s.name)
            plans = [p.plan for p in sub_doc.plans]
            plan_names = ", ".join(plans) if plans else "No Plan"
        except Exception:
            plan_names = "Error Loading Plan"
            
        result.append({
            "name": s.name,
            "status": s.status,
            "start_date": s.start_date,
            "plans": plan_names
        })
        
    return result
@frappe.whitelist()
def activate_existing_subscription(subscription_name):
    """
    Reactivates a selected existing subscription and forces its 
    start date to the 1st of the current month.
    """
    sub_doc = frappe.get_doc("Subscription", subscription_name)
    
    # Force start date and billing cycle start to the 1st of the current month
    today = getdate(nowdate())
    first_of_month = today.replace(day=1).strftime("%Y-%m-%d")
    
    sub_doc.status = "Active"
    sub_doc.start_date = first_of_month
    sub_doc.current_invoice_start = first_of_month
    
    sub_doc.save(ignore_permissions=True)
    frappe.db.commit()
    
    return sub_doc.name
    
@frappe.whitelist()
def peek_next_id(customer_group):

    series = frappe.db.get_value(
        "Customer Group",
        customer_group,
        "custom_naming_series",
    )

    if not series or "#" not in series:
        return ""

    prefix = series.split("#")[0]
    hash_count = series.count("#")

    query = frappe.db.sql(
        """
        SELECT current
        FROM `tabSeries`
        WHERE name = %s
        """,
        prefix,
    )

    current = query[0][0] if query else 0

    next_num = int(current) + 1

    return f"{prefix}{str(next_num).zfill(hash_count)}"


@frappe.whitelist()
def get_active_subscription(member_id):
    """
    Fetches the live subscription (Active, Unpaid, or Past Due Date) 
    and its linked plans for display on the Club Member form.
    """
    active_sub = frappe.get_all(
        "Subscription",
        filters={
            "party_type": "Customer",
            "party": member_id,
            # Broaden the filter to catch live but unpaid subscriptions
            "status": ["in", ["Active", "Unpaid", "Past Due Date"]] 
        },
        fields=["name", "status"],
        limit=1
    )

    if not active_sub:
        return None

    # Fetch the specific plans attached to this subscription
    sub_doc = frappe.get_doc("Subscription", active_sub[0].name)
    plans = [p.plan for p in sub_doc.plans]

    return {
        "subscription_id": sub_doc.name,
        "status": active_sub[0].status, # Pass the status to the frontend
        "plans": ", ".join(plans)
    }
@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def filter_active_customers(doctype, txt, searchfield, start, page_len, filters):
    """
    Custom query for the Customer Select box. Hides any customer 
    whose linked Club Member profile is marked as 'Inactive'.
    """
    return frappe.db.sql("""
        SELECT name, customer_name
        FROM `tabCustomer`
        WHERE disabled = 0
        AND name NOT IN (
            SELECT customer FROM `tabClub Member` 
            WHERE status = 'Inactive' AND customer IS NOT NULL
        )
        AND (name LIKE %(txt)s OR customer_name LIKE %(txt)s)
        ORDER BY name
        LIMIT %(start)s, %(page_len)s
    """, {
        'txt': '%' + txt + '%',
        'start': start,
        'page_len': page_len
    })
    
def update_member_on_subscription_save(doc, method):
    """
    Master controller hook: Listens exclusively to the Subscription module.
    When a subscription is created or updated, it pushes the status 
    and plan text down to the linked Club Member profile.
    """
    if doc.party_type != "Customer":
        return
        
    # Find the linked Club Member record using the Customer ID
    member_name = frappe.db.get_value("Club Member", {"customer": doc.party}, "name")
    if not member_name:
        # Fallback: check if the customer name/id matches the Club Member primary key directly
        if frappe.db.exists("Club Member", doc.party):
            member_name = doc.party
        else:
            return
            
    # 1. Compile readable plan text
    try:
        plans = [p.plan for p in doc.plans]
        display_text = ", ".join(plans) if plans else "No Active Plan"
    except Exception:
        display_text = "Error Loading Plan"
        
    # 2. Determine if the subscription is in a live state
    is_live = doc.status in ["Active", "Unpaid", "Past Due Date"]
    new_status = "Active" if is_live else "Inactive"
    
    # 3. Push updates directly to the Club Member profile
    frappe.db.set_value("Club Member", member_name, {
        "status": new_status,
        "current_subscription": display_text
    }, update_modified=False)