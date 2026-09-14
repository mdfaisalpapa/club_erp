frappe.ui.form.on("Club Membership Plan", {

    refresh(frm) {

        // ------------------------------------------------------------
        // Identity fields
        //
        // These fields are Read Only in the DocType.
        // They are enabled only when creating a NEW plan.
        // ------------------------------------------------------------

        if (frm.is_new()) {

            frm.set_df_property("item_code", "read_only", 0);
            frm.set_df_property("plan_name", "read_only", 0);
            frm.set_df_property("customer_group", "read_only", 0);

        } else {

            frm.set_df_property("item_code", "read_only", 1);
            frm.set_df_property("plan_name", "read_only", 1);
            frm.set_df_property("customer_group", "read_only", 1);

        }

        // Membership Item is ALWAYS read-only.
        frm.set_df_property("membership_item", "read_only", 1);


        // ------------------------------------------------------------
        // Open Membership Item
        // ------------------------------------------------------------

        if (!frm.is_new() && frm.doc.membership_item) {

            frm.add_custom_button(
                __("Open Membership Item"),
                function () {

                    frappe.set_route(
                        "Form",
                        "Item",
                        frm.doc.membership_item
                    );

                }
            );

        }


        // ------------------------------------------------------------
        // Dashboard status
        // ------------------------------------------------------------

        if (!frm.is_new()) {

            if (frm.doc.membership_item) {

                frm.dashboard.add_indicator(
                    __("Item Created"),
                    "green"
                );

            } else {

                frm.dashboard.add_indicator(
                    __("Membership Item Not Created"),
                    "orange"
                );

            }

        }


        // ------------------------------------------------------------
        // Customer Group: only leaf groups
        // ------------------------------------------------------------

        frm.set_query("customer_group", function () {

            return {
                filters: {
                    is_group: 0
                }
            };

        });

    },


    // ------------------------------------------------------------
    // Institutional Membership
    // ------------------------------------------------------------

    is_institutional(frm) {

        if (!frm.doc.is_institutional) {

            frm.set_value(
                "allowed_nominees",
                0
            );

        }

    },


    // ------------------------------------------------------------
    // Client-side validation
    // ------------------------------------------------------------

    validate(frm) {

        // Institutional membership must have nominees.

        if (
            frm.doc.is_institutional &&
            cint(frm.doc.allowed_nominees) <= 0
        ) {

            frappe.throw(
                __(
                    "Allowed Nominees must be greater than zero for an institutional membership plan."
                )
            );

        }


        // 0 = lifetime membership.

        if (
            cint(frm.doc.validity_in_years) < 0
        ) {

            frappe.throw(
                __("Validity in Years cannot be negative.")
            );

        }


        // Membership rate cannot be negative.

        if (
            flt(frm.doc.membership_rate) < 0
        ) {

            frappe.throw(
                __("Membership Rate cannot be negative.")
            );

        }

    }

});
