import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ClubMembershipPlan(Document):

    ITEM_GROUP = "Membership Fees"
    ITEM_UOM = "Nos"
    ITEM_HSN = "999599"

    # ------------------------------------------------------------------
    # DOCUMENT EVENTS
    # ------------------------------------------------------------------

    def validate(self):
        self.validate_membership_rules()
        self.validate_item_code()
        self.validate_customer_group_price_list()

    def after_insert(self):
        """
        Create the ERPNext Item and its Item Price automatically
        for a genuinely new Membership Plan.

        If membership_item is already supplied, it means an existing
        Item is being linked to this Plan (such as during migration).
        In that case, do NOT modify the existing Item Price.
        """

        if not self.membership_item:
            self.create_membership_item()
            self.sync_membership_price()

    def on_update(self):
        """
        Keep the Item Price synchronized with the Membership Plan
        after the Plan has been created.
        """

        if not self.membership_item:
            return

        self.sync_membership_price()

    # ------------------------------------------------------------------
    # VALIDATION
    # ------------------------------------------------------------------

    def validate_membership_rules(self):
        """
        Validate membership-specific business rules.

        Validity:
            0 = lifetime membership
            >0 = number of years

        Institutional membership:
            Must have at least one nominee.

        Non-institutional membership:
            Nominees are always zero.

        Membership Rate:
            Zero is permitted.
            Negative values are not permitted.
        """

        if self.validity_in_years is None:
            frappe.throw(
                _("Validity in Years is mandatory.")
            )

        if flt(self.validity_in_years) < 0:
            frappe.throw(
                _("Validity in Years cannot be negative.")
            )

        if self.is_institutional:

            if flt(self.allowed_nominees) <= 0:
                frappe.throw(
                    _(
                        "Allowed Nominees must be greater than zero "
                        "for an institutional membership plan."
                    )
                )

        else:

            # Non-institutional memberships cannot have nominees.
            self.allowed_nominees = 0

        if self.membership_rate is None:
            frappe.throw(
                _("Membership Rate is mandatory.")
            )

        if flt(self.membership_rate) < 0:
            frappe.throw(
                _("Membership Rate cannot be negative.")
            )

    def validate_item_code(self):
        """
        Item Code is supplied by the user and becomes the ERPNext
        Item Code.

        Once the Membership Item has been created, Item Code cannot
        be changed.
        """

        if not self.item_code:
            frappe.throw(
                _("Item Code is mandatory.")
            )

        # --------------------------------------------------------------
        # Existing Plan
        # --------------------------------------------------------------

        if self.membership_item:

            linked_item_code = frappe.db.get_value(
                "Item",
                self.membership_item,
                "item_code"
            )

            if linked_item_code and linked_item_code != self.item_code:
                frappe.throw(
                    _(
                        "Item Code cannot be changed after the "
                        "Membership Item has been created."
                    )
                )

            return

        # --------------------------------------------------------------
        # New Plan
        # --------------------------------------------------------------

        existing_item = frappe.db.get_value(
            "Item",
            self.item_code,
            "name"
        )

        if not existing_item:
            return

        existing_plan = frappe.db.get_value(
            "Club Membership Plan",
            {
                "membership_item": existing_item
            },
            "name"
        )

        if existing_plan:
            frappe.throw(
                _(
                    "Item {0} is already linked to Club Membership "
                    "Plan {1}."
                ).format(
                    frappe.bold(self.item_code),
                    frappe.bold(existing_plan)
                )
            )

        frappe.throw(
            _(
                "Item {0} already exists. Please use a different "
                "Item Code."
            ).format(
                frappe.bold(self.item_code)
            )
        )

    def validate_customer_group_price_list(self):
        """
        Validate Customer Group and its Default Selling Price List.

        Customer Group is part of the pricing identity of a
        Membership Plan and cannot be changed after the Membership
        Item has been created.
        """

        # --------------------------------------------------------------
        # Customer Group is mandatory
        # --------------------------------------------------------------

        if not self.customer_group:
            frappe.throw(
                _("Customer Group is mandatory.")
            )

        # --------------------------------------------------------------
        # Customer Group cannot change after Item creation
        # --------------------------------------------------------------

        if self.membership_item:

            old_doc = self.get_doc_before_save()

            if (
                old_doc
                and old_doc.customer_group != self.customer_group
            ):
                frappe.throw(
                    _(
                        "Customer Group cannot be changed after "
                        "the Membership Item has been created."
                    )
                )

        # --------------------------------------------------------------
        # Get Customer Group
        # --------------------------------------------------------------

        group = frappe.db.get_value(
            "Customer Group",
            self.customer_group,
            [
                "is_group",
                "default_price_list"
            ],
            as_dict=True
        )

        if not group:
            frappe.throw(
                _(
                    "Customer Group {0} does not exist."
                ).format(
                    frappe.bold(self.customer_group)
                )
            )

        # --------------------------------------------------------------
        # Must be a leaf Customer Group
        # --------------------------------------------------------------

        if group.is_group:
            frappe.throw(
                _(
                    "Customer Group {0} is a group. "
                    "Please select a leaf Customer Group."
                ).format(
                    frappe.bold(self.customer_group)
                )
            )

        # --------------------------------------------------------------
        # Default Price List
        # --------------------------------------------------------------

        price_list = group.default_price_list

        if not price_list:
            frappe.throw(
                _(
                    "Customer Group {0} does not have a "
                    "Default Price List."
                ).format(
                    frappe.bold(self.customer_group)
                )
            )

        # --------------------------------------------------------------
        # Validate Price List
        # --------------------------------------------------------------

        price_list_details = frappe.db.get_value(
            "Price List",
            price_list,
            [
                "enabled",
                "selling"
            ],
            as_dict=True
        )

        if not price_list_details:
            frappe.throw(
                _(
                    "Price List {0} does not exist."
                ).format(
                    frappe.bold(price_list)
                )
            )

        if not price_list_details.enabled:
            frappe.throw(
                _(
                    "Price List {0} is disabled."
                ).format(
                    frappe.bold(price_list)
                )
            )

        if not price_list_details.selling:
            frappe.throw(
                _(
                    "Price List {0} is not a Selling Price List."
                ).format(
                    frappe.bold(price_list)
                )
            )

    # ------------------------------------------------------------------
    # ITEM CREATION
    # ------------------------------------------------------------------

    def create_membership_item(self):
        """
        Create the ERPNext Item represented by this Membership Plan.
        """

        if frappe.db.exists("Item", self.item_code):
            frappe.throw(
                _(
                    "Item {0} already exists. Cannot create the "
                    "Membership Item."
                ).format(
                    frappe.bold(self.item_code)
                )
            )

        item = frappe.new_doc("Item")

        # --------------------------------------------------------------
        # Basic Item identity
        # --------------------------------------------------------------

        item.item_code = self.item_code
        item.item_name = self.plan_name

        # --------------------------------------------------------------
        # Membership Items are service/non-stock Items
        # --------------------------------------------------------------

        item.item_group = self.ITEM_GROUP
        item.stock_uom = self.ITEM_UOM
        item.is_stock_item = 0
        item.is_sales_item = 1
        item.is_purchase_item = 0

        # --------------------------------------------------------------
        # HSN/SAC
        # --------------------------------------------------------------

        item.gst_hsn_code = self.ITEM_HSN

        # --------------------------------------------------------------
        # Current Item customization makes Standard Rate mandatory
        # --------------------------------------------------------------

        item.standard_rate = 0

        item.insert(
            ignore_permissions=True,
            ignore_mandatory=True
        )

        # Update in-memory document
        self.membership_item = item.name

        # Update database
        self.db_set(
            "membership_item",
            item.name,
            update_modified=False
        )

        frappe.msgprint(
            _(
                "Membership Item {0} created successfully."
            ).format(
                frappe.bold(item.name)
            ),
            indicator="green",
            alert=True
        )

    # ------------------------------------------------------------------
    # ITEM PRICE
    # ------------------------------------------------------------------

    def get_membership_price_list(self):
        """
        Get the Price List through:

            Membership Plan
                    ↓
            Customer Group
                    ↓
            Default Price List
        """

        price_list = frappe.db.get_value(
            "Customer Group",
            self.customer_group,
            "default_price_list"
        )

        if not price_list:
            frappe.throw(
                _(
                    "Customer Group {0} does not have a "
                    "Default Price List."
                ).format(
                    frappe.bold(self.customer_group)
                )
            )

        return price_list

    def sync_membership_price(self):
        """
        Create or update the Item Price for this Membership Plan.

        One Membership Plan has:

            - one Customer Group
            - one Default Price List
            - one Membership Rate
            - one Item Price in Nos
        """

        if not self.membership_item:
            return

        price_list = self.get_membership_price_list()

        existing_price = frappe.db.get_value(
            "Item Price",
            {
                "item_code": self.membership_item,
                "price_list": price_list,
                "uom": self.ITEM_UOM,
                "selling": 1,
            },
            "name"
        )

        if existing_price:

            item_price = frappe.get_doc(
                "Item Price",
                existing_price
            )

            item_price.price_list_rate = self.membership_rate
            item_price.selling = 1
            item_price.buying = 0

            item_price.save(
                ignore_permissions=True
            )

        else:

            item_price = frappe.new_doc("Item Price")

            item_price.item_code = self.membership_item
            item_price.price_list = price_list
            item_price.uom = self.ITEM_UOM
            item_price.price_list_rate = self.membership_rate
            item_price.selling = 1
            item_price.buying = 0

            item_price.insert(
                ignore_permissions=True
            )
