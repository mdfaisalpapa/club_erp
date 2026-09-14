frappe.ui.form.on("Club Member", {

    // ---------------------------------------------------------------
    // SETUP
    // ---------------------------------------------------------------

    setup: function(frm) {

        // Membership Plan is now a Link to Club Membership Plan.
        // Filter directly using the Plan's Customer Group.
        frm.set_query("membership_plan", function() {

            if (!frm.doc.customer_group) {
                return {};
            }

            return {
                filters: {
                    customer_group: frm.doc.customer_group,
                    enabled: 1
                }
            };
        });

        // Parent Institution should only show Institutional Members.
        frm.set_query("parent_institution", function() {

            return {
                filters: {
                    customer_group: "Institutional Member",
                    status: ["not in", ["Inactive", "Suspended"]]
                }
            };
        });
    },


    // ---------------------------------------------------------------
    // CUSTOMER GROUP
    // ---------------------------------------------------------------

    customer_group: function(frm) {

        if (!frm.is_new()) {
            return;
        }

        if (!frm.doc.customer_group) {
            frm.set_df_property(
                "customer_group",
                "description",
                ""
            );

            return;
        }

        // -----------------------------------------------------------
        // NEXT MEMBER NUMBER PREVIEW
        // -----------------------------------------------------------

        frappe.call({
            method:
                "club_erp.club_erp.doctype.club_member.club_member.peek_next_id",

            args: {
                customer_group: frm.doc.customer_group
            },

            callback: function(r) {

                if (r.message) {

                    frm.set_df_property(
                        "customer_group",
                        "description",
                        `Next ID Sequence:
                        <span style="font-weight:bold;">
                            ${r.message}
                        </span>`
                    );

                } else {

                    frm.set_df_property(
                        "customer_group",
                        "description",
                        ""
                    );
                }
            }
        });


        // -----------------------------------------------------------
        // RESET DEPENDENT FIELDS
        // -----------------------------------------------------------

        frm.set_value("membership_plan", "");
        frm.set_value("membership_expiry_date", "");
        frm.set_value("membership_start_date", "");
        frm.set_value("original_join_date", "");
        frm.set_value("parent_institution", "");
        frm.set_value("is_institution", 0);


        // -----------------------------------------------------------
        // MEMBER / NON-MEMBER
        // -----------------------------------------------------------

        const member_groups = [
            "Institutional Member",
            "Institutional Nominee",
            "Other Government Officer",
            "Private Member",
            "Railway Life Member",
            "Railway Non Life Member",
            "Railway Ward Member"
        ];

        const is_member =
            member_groups.includes(frm.doc.customer_group);

        const is_institution =
            frm.doc.customer_group === "Institutional Member";

        frm.set_value(
            "is_institution",
            is_institution ? 1 : 0
        );


        // -----------------------------------------------------------
        // MEMBERSHIP FIELD VISIBILITY
        // -----------------------------------------------------------

        if (is_member) {

            frm.set_df_property(
                "membership_plan",
                "hidden",
                0
            );

            frm.set_df_property(
                "membership_plan",
                "reqd",
                1
            );

            frm.set_df_property(
                "membership_start_date",
                "hidden",
                0
            );

            frm.set_df_property(
                "membership_start_date",
                "reqd",
                1
            );

            const is_nominee =
                frm.doc.customer_group ===
                "Institutional Nominee";

            frm.set_df_property(
                "membership_plan",
                "read_only",
                is_nominee ? 1 : 0
            );

            frm.set_df_property(
                "membership_start_date",
                "read_only",
                is_nominee ? 1 : 0
            );

            frm.set_df_property(
                "membership_expiry_date",
                "read_only",
                is_nominee ? 1 : 0
            );

            frm.set_df_property(
                "original_join_date",
                "read_only",
                is_nominee ? 1 : 0
            );

        } else {

            frm.set_df_property(
                "membership_plan",
                "hidden",
                1
            );

            frm.set_df_property(
                "membership_plan",
                "reqd",
                0
            );

            frm.set_df_property(
                "membership_start_date",
                "hidden",
                1
            );

            frm.set_df_property(
                "membership_start_date",
                "reqd",
                0
            );

            frm.set_df_property(
                "membership_expiry_date",
                "hidden",
                1
            );

            frm.set_df_property(
                "membership_expiry_date",
                "reqd",
                0
            );
        }
    },


    // ---------------------------------------------------------------
    // PARENT INSTITUTION
    // ---------------------------------------------------------------

    parent_institution: function(frm) {

        if (!frm.is_new()) {
            return;
        }

        if (
            frm.doc.customer_group !==
            "Institutional Nominee"
        ) {
            return;
        }

        if (!frm.doc.parent_institution) {
            return;
        }


        frappe.db.get_doc(
            "Club Member",
            frm.doc.parent_institution
        ).then(parent => {

            if (!parent) {
                return;
            }

            const parent_plan =
                parent.membership_plan;

            const parent_start =
                parent.membership_start_date;

            const parent_expiry =
                parent.membership_expiry_date;


            // -------------------------------------------------------
            // Parent must have a Membership Plan
            // -------------------------------------------------------

            if (!parent_plan) {

                frappe.msgprint({
                    title: __("Error"),
                    indicator: "red",
                    message:
                        "The selected Parent Institution does not " +
                        "have a Membership Plan."
                });

                frm.set_value(
                    "parent_institution",
                    ""
                );

                return;
            }


            // -------------------------------------------------------
            // Inherit Plan and dates
            // -------------------------------------------------------

            frm.set_value(
                "membership_plan",
                parent_plan
            );

            if (parent_start) {

                frm.set_value(
                    "membership_start_date",
                    parent_start
                );

                frm.set_value(
                    "original_join_date",
                    parent_start
                );
            }

            if (parent_expiry) {

                frm.set_value(
                    "membership_expiry_date",
                    parent_expiry
                );

                frm.set_df_property(
                    "membership_expiry_date",
                    "hidden",
                    0
                );
            }


            // -------------------------------------------------------
            // Get nominee limit directly from Club Membership Plan
            // -------------------------------------------------------

            frappe.db.get_value(
                "Club Membership Plan",
                parent_plan,
                [
                    "allowed_nominees",
                    "enabled",
                    "is_institutional"
                ]
            ).then(plan_result => {

                const plan =
                    plan_result.message;

                if (!plan) {

                    frappe.msgprint({
                        title: __("Error"),
                        indicator: "red",
                        message:
                            "The selected Membership Plan could " +
                            "not be found."
                    });

                    frm.set_value(
                        "parent_institution",
                        ""
                    );

                    return;
                }


                if (!plan.enabled) {

                    frappe.msgprint({
                        title: __("Error"),
                        indicator: "red",
                        message:
                            "The Parent Institution's Membership " +
                            "Plan is disabled."
                    });

                    frm.set_value(
                        "parent_institution",
                        ""
                    );

                    return;
                }


                if (!plan.is_institutional) {

                    frappe.msgprint({
                        title: __("Error"),
                        indicator: "red",
                        message:
                            "The Parent Institution does not have " +
                            "an institutional Membership Plan."
                    });

                    frm.set_value(
                        "parent_institution",
                        ""
                    );

                    return;
                }


                const max_limit =
                    cint(plan.allowed_nominees || 0);


                // ---------------------------------------------------
                // Count existing active nominees
                // ---------------------------------------------------

                frappe.db.get_list(
                    "Club Member",
                    {
                        filters: {
                            parent_institution:
                                frm.doc.parent_institution,

                            status: ["!=", "Inactive"]
                        },

                        fields: ["name"],

                        limit: 1000
                    }
                ).then(active_nominees => {

                    const active_count =
                        active_nominees.length;


                    if (active_count >= max_limit) {

                        frappe.msgprint({
                            title: __("Limit Reached"),
                            indicator: "red",
                            message:
                                `This institution has reached its ` +
                                `limit of <b>${max_limit} ` +
                                `active nominees</b>.`
                        });

                        frm.set_value(
                            "parent_institution",
                            ""
                        );

                        frm.set_value(
                            "membership_plan",
                            ""
                        );

                        frm.set_value(
                            "naming_series",
                            ""
                        );

                        return;
                    }


                    // ------------------------------------------------
                    // Nominee naming series
                    // ------------------------------------------------

                    frm.set_value(
                        "naming_series",
                        frm.doc.parent_institution + "-#"
                    );
                });
            });
        });
    },


    // ---------------------------------------------------------------
    // MEMBERSHIP PLAN
    // ---------------------------------------------------------------

    membership_plan: function(frm) {

        if (
            !frm.is_new() ||
            !frm.doc.membership_plan
        ) {
            return;
        }


        frappe.db.get_value(
            "Club Membership Plan",
            frm.doc.membership_plan,
            [
                "enabled",
                "customer_group",
                "membership_item",
                "validity_in_years",
                "is_institutional",
                "allowed_nominees",
                "membership_rate"
            ]
        ).then(r => {

            const plan = r.message;

            if (!plan) {
                return;
            }


            // -------------------------------------------------------
            // Basic validation
            // -------------------------------------------------------

            if (!plan.enabled) {

                frappe.msgprint({
                    title: __("Invalid Membership Plan"),
                    indicator: "red",
                    message:
                        "The selected Membership Plan is disabled."
                });

                frm.set_value(
                    "membership_plan",
                    ""
                );

                return;
            }


            if (
                plan.customer_group !==
                frm.doc.customer_group
            ) {

                frappe.msgprint({
                    title: __("Invalid Membership Plan"),
                    indicator: "red",
                    message:
                        `The selected Membership Plan belongs to ` +
                        `<b>${plan.customer_group}</b>.`
                });

                frm.set_value(
                    "membership_plan",
                    ""
                );

                return;
            }


            if (!plan.membership_item) {

                frappe.msgprint({
                    title: __("Invalid Membership Plan"),
                    indicator: "red",
                    message:
                        "The selected Membership Plan does not " +
                        "have a Membership Item."
                });

                frm.set_value(
                    "membership_plan",
                    ""
                );

                return;
            }


            // -------------------------------------------------------
            // Institutional flag
            //
            // Keep this as a property of the MEMBER based on the
            // Customer Group. The Plan's is_institutional is used
            // for validation.
            // -------------------------------------------------------

            frm.set_value(
                "is_institution",
                frm.doc.customer_group ===
                "Institutional Member" ? 1 : 0
            );


            // -------------------------------------------------------
            // Membership validity
            // -------------------------------------------------------

            const years =
                cint(plan.validity_in_years || 0);


            // Lifetime membership
            if (years === 0) {

                frm.set_value(
                    "membership_expiry_date",
                    ""
                );

                frm.set_df_property(
                    "membership_expiry_date",
                    "hidden",
                    1
                );

                frm.set_df_property(
                    "membership_expiry_date",
                    "reqd",
                    0
                );

            } else {

                let start_date =
                    frm.doc.membership_start_date ||
                    frappe.datetime.get_today();

                let expiry_date =
                    frappe.datetime.add_days(
                        frappe.datetime.add_months(
                            start_date,
                            years * 12
                        ),
                        -1
                    );

                frm.set_value(
                    "membership_expiry_date",
                    expiry_date
                );

                if (!frm.doc.membership_start_date) {

                    frm.set_value(
                        "membership_start_date",
                        start_date
                    );

                    frm.set_value(
                        "original_join_date",
                        start_date
                    );
                }

                frm.set_df_property(
                    "membership_expiry_date",
                    "hidden",
                    0
                );

                frm.set_df_property(
                    "membership_expiry_date",
                    "reqd",
                    1
                );
            }
        });
    },


    // ---------------------------------------------------------------
    // MEMBERSHIP START DATE
    // ---------------------------------------------------------------

    membership_start_date: function(frm) {

        if (
            !frm.is_new() ||
            frm.doc.customer_group ===
                "Institutional Nominee"
        ) {
            return;
        }

        if (frm.doc.membership_start_date) {

            frm.set_value(
                "original_join_date",
                frm.doc.membership_start_date
            );
        }

        if (frm.doc.membership_plan) {

            frm.trigger(
                "membership_plan"
            );
        }
    },


    // ---------------------------------------------------------------
    // REFRESH
    // ---------------------------------------------------------------

    refresh: function(frm) {

        // -----------------------------------------------------------
        // Lock Member Name after creation
        // -----------------------------------------------------------

        if (!frm.is_new()) {

            frm.set_df_property(
                "member_name",
                "read_only",
                1
            );
        }


        // -----------------------------------------------------------
        // Retire Nominee button
        // -----------------------------------------------------------

        if (
            frm.doc.customer_group ===
                "Institutional Nominee" &&
            frm.doc.status === "Active" &&
            !frm.is_new()
        ) {

            frm.add_custom_button(
                __("Retire Nominee"),
                function() {

                    frappe.confirm(
                        "Retire this nominee? This sets the " +
                        "status to Inactive and frees the " +
                        "institutional quota slot.",

                        function() {

                            frm.set_value(
                                "status",
                                "Inactive"
                            );

                            frm.save().then(() => {

                                frappe.msgprint({
                                    title: __("Success"),
                                    indicator: "green",
                                    message:
                                        "Nominee Retired!"
                                });
                            });
                        }
                    );
                }
            ).addClass("btn-danger");
        }
    }
});
