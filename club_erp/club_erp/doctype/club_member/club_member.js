frappe.ui.form.on("Club Member", {
    refresh: function(frm) {
        
        // -----------------------------------------------------------
        // 1. Fetch and Display Subscription Badge & Management Actions
        // -----------------------------------------------------------
        if (!frm.is_new()) {
            frappe.call({
                method: "club_erp.club_erp.doctype.club_member.club_member.get_active_subscription",
                args: {
                    member_id: frm.doc.name
                },
                callback: function(r) {
                    
                    let html_badge = "";
                    
                    if (r.message) {
                        // Found a Live/Unpaid Subscription -> GREEN / ORANGE BADGE
                        let sub_id = r.message.subscription_id;
                        let plan_names = r.message.plans;
                        let sub_status = r.message.status;
                        
                        let bg_color = sub_status === "Active" ? "#e8f5e9" : "#fff3e0";
                        let border_color = sub_status === "Active" ? "#c8e6c9" : "#ffe0b2";
                        let text_color = sub_status === "Active" ? "#2e7d32" : "#e65100";
                        
                        html_badge = `
                            <div style="padding: 10px 15px; margin-bottom: 15px; background-color: ${bg_color}; color: ${text_color}; border-radius: 6px; border: 1px solid ${border_color}; display: flex; align-items: center; justify-content: space-between;">
                                <div>
                                    <span style="font-size: 12px; text-transform: uppercase; font-weight: bold; letter-spacing: 0.5px;">Subscription Status: ${sub_status}</span><br>
                                    <span style="font-size: 15px; font-weight: 500;">${plan_names}</span>
                                </div>
                                <a href="/app/subscription/${sub_id}" class="btn btn-xs btn-default" style="background-color: white; border-color: ${border_color}; color: ${text_color}; font-weight: bold; text-decoration: none;">
                                    View Record
                                </a>
                            </div>
                        `;
                    } else {
                        // No Live Subscription Found -> GRAY BADGE WITH MANAGEMENT BUTTON
                        html_badge = `
                            <div style="padding: 10px 15px; margin-bottom: 15px; background-color: #f5f5f5; color: #616161; border-radius: 6px; border: 1px solid #e0e0e0; display: flex; align-items: center; justify-content: space-between;">
                                <div>
                                    <span style="font-size: 12px; text-transform: uppercase; font-weight: bold; letter-spacing: 0.5px;">Subscription Status</span><br>
                                    <span style="font-size: 15px; font-weight: 500;">No Active Subscription Linked</span>
                                </div>
                                <button id="reactivate_sub_btn" class="btn btn-xs btn-primary" style="font-weight: bold;">
                                    + Manage / Activate Subscription
                                </button>
                            </div>
                        `;
                    }
                    
                    // Unhide the HTML field and inject the content
                    frm.set_df_property("active_subscription_display", "hidden", 0);
                    frm.get_field("active_subscription_display").$wrapper.html(html_badge);
                    
                    // Bind click event to check for inactive/historical subscriptions
                    frm.get_field("active_subscription_display").$wrapper.find("#reactivate_sub_btn").on("click", function() {
                        frappe.call({
                            method: "club_erp.club_erp.doctype.club_member.club_member.get_inactive_subscriptions",
                            args: { party_name: frm.doc.name },
                            callback: function(res) {
                                let inactive_subs = res.message || [];
                                
                                // Format options to display the readable plan name first
                                let options = inactive_subs.map(s => `${s.plans} (${s.status} - Started: ${s.start_date}) [${s.name}]`);
                                
                                // Always append the choice to create a brand new subscription
                                options.push("+ Create New Subscription");
                                
                                let d = new frappe.ui.Dialog({
                                    title: __("Manage Subscriptions"),
                                    fields: [
                                        {
                                            label: __("Select Existing Subscription or Create New"),
                                            fieldname: "sub_name",
                                            fieldtype: "Select",
                                            options: options,
                                            reqd: 1
                                        }
                                    ],
                                    primary_action_label: __("Proceed"),
                                    primary_action(values) {
                                        d.hide();
                                        
                                        // If the clerk chose to create a new one, redirect them
                                        if (values.sub_name === "+ Create New Subscription") {
                                            window.location.href = `/app/subscription/new?party_type=Customer&party=${frm.doc.name}`;
                                            return;
                                        }
                                        
                                        // Otherwise, extract the subscription ID and activate it
                                        let match = values.sub_name.match(/\[(.*?)\]/);
                                        let selected_id = match ? match[1] : null;
                                        
                                        if (!selected_id) {
                                            frappe.msgprint(__("Could not determine the selected subscription ID."));
                                            return;
                                        }
                                        
                                        frappe.call({
                                            method: "club_erp.club_erp.doctype.club_member.club_member.activate_existing_subscription",
                                            args: { subscription_name: selected_id },
                                            callback: function() {
                                                frappe.show_alert({message: __("Subscription activated successfully!"), indicator: "green"});
                                                frm.reload();
                                            }
                                        });
                                    }
                                });
                                d.show();
                            }
                        });
                    });
                }
            });
        }

        // -----------------------------------------------------------
        // 2. Lock Member Name after creation
        // -----------------------------------------------------------
        if (!frm.is_new()) {
            frm.set_df_property("member_name", "read_only", 1);
        }

        // -----------------------------------------------------------
        // 3. Retire Nominee button
        // -----------------------------------------------------------
        if (frm.doc.customer_group === "Institutional Nominee" && frm.doc.status === "Active" && !frm.is_new()) {
            frm.add_custom_button(__("Retire Nominee"), function() {
                frappe.confirm(
                    "Retire this nominee? This sets the status to Inactive and frees the institutional quota slot.",
                    function() {
                        frm.set_value("status", "Inactive");
                        frm.save().then(() => {
                            frappe.msgprint({ title: __("Success"), indicator: "green", message: "Nominee Retired!" });
                        });
                    }
                );
            }).addClass("btn-danger");
        }
    },

    // ---------------------------------------------------------------
    // 4. Status Dropdown UI Validation Safeguard
    // ---------------------------------------------------------------
    status: function(frm) {
        if (frm.doc.status === "Active" && !frm.is_new()) {
            frappe.call({
                method: "club_erp.club_erp.doctype.club_member.club_member.get_active_subscription",
                args: {
                    member_id: frm.doc.name
                },
                callback: function(r) {
                    if (!r.message) {
                        frappe.msgprint({
                            title: __("Activation Blocked"),
                            indicator: "red",
                            message: "You must create and submit a Subscription for this member before setting their status to Active."
                        });
                        frm.set_value("status", "Inactive");
                    }
                }
            });
        }
    }
});