import frappe
from frappe.model.document import Document
from frappe.utils import today, date_diff
from frappe.model.naming import make_autoname


class ClubMember(Document):

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
