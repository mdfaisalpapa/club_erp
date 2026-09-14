frappe.ui.form.on('Club Member', {
    setup: function(frm) {
        // Filter Membership Plans by the selected Member Group
        frm.set_query("membership_plan", function() {
            if (frm.doc.customer_group) return { filters: { "custom_customer_group": frm.doc.customer_group } };
        });
    },

    customer_group: function(frm) {
        // Only automate these settings for new records
        if (!frm.is_new() || !frm.doc.customer_group) {
            frm.set_df_property('customer_group', 'description', '');
            return;
        }

        // --- FETCH AND DISPLAY NEXT ID PREVIEW ---
        frappe.call({
            method: "club_erp.club_erp.doctype.club_member.club_member.peek_next_id",
            args: { customer_group: frm.doc.customer_group },
            callback: function(r) {
                if (r.message) {
                    frm.set_df_property('customer_group', 'description', `Next ID Sequence: <span style="color:green; font-weight:bold;">${r.message}</span>`);
                } else {
                    frm.set_df_property('customer_group', 'description', '');
                }
            }
        });

        // Reset dependent fields on group change
        frm.set_value('membership_plan', '');
        frm.set_value('membership_expiry_date', '');
        frm.set_value('membership_start_date', '');
        frm.set_value('original_join_date', '');
        frm.set_value('parent_institution', '');
        frm.set_value('is_institution', 0);

        // --- NAMING SERIES & INSTITUTION FLAG LOGIC ---
        let series = '';
        let is_member = 1; 
        let is_inst = 0;
        
        switch(frm.doc.customer_group) {
            case 'Railway Life Member':
            case 'Railway Non Life Member': series = '3.###'; break;
            case 'Other Government Officer': series = '4.###'; break;
            case 'Railway Ward Member': series = '5.###'; break;
            case 'Private Member': series = '7.###'; break;
            case 'Institutional Member': 
                series = '9.###'; 
                is_inst = 1; 
                break;
            case 'Institutional Nominee': series = ''; break; 
            case 'Non Member': series = '8.###'; is_member = 0; break;
            default: series = 'MEM-.YYYY.-.####'; is_member = 0;
        }

        if (series) frm.set_value('naming_series', series);
        frm.set_value('is_institution', is_inst);

        // --- UI VISIBILITY & READ-ONLY LOGIC ---
        if (is_member === 1) {
            frm.set_df_property('membership_plan', 'hidden', 0);
            frm.set_df_property('membership_plan', 'reqd', 1);
            frm.set_df_property('membership_start_date', 'hidden', 0);
            frm.set_df_property('membership_start_date', 'reqd', 1);

            let is_nominee = (frm.doc.customer_group === 'Institutional Nominee');
            frm.set_df_property('membership_plan', 'read_only', is_nominee);
            frm.set_df_property('membership_start_date', 'read_only', is_nominee);
            frm.set_df_property('membership_expiry_date', 'read_only', is_nominee);
            frm.set_df_property('original_join_date', 'read_only', is_nominee);
        } else {
            frm.set_df_property('membership_plan', 'hidden', 1);
            frm.set_df_property('membership_plan', 'reqd', 0);
            frm.set_df_property('membership_start_date', 'hidden', 1);
            frm.set_df_property('membership_start_date', 'reqd', 0);
            frm.set_df_property('membership_expiry_date', 'hidden', 1);
            frm.set_df_property('membership_expiry_date', 'reqd', 0);
        }
    },

    parent_institution: function(frm) {
        if (!frm.is_new()) return;

        if (frm.doc.customer_group === 'Institutional Nominee' && frm.doc.parent_institution) {
            frappe.call({
                method: 'frappe.client.get',
                args: { doctype: 'Club Member', name: frm.doc.parent_institution },
                callback: function(parent_r) {
                    if (!parent_r.message) return;
                    let parent_plan = parent_r.message.membership_plan;
                    let parent_start = parent_r.message.membership_start_date;
                    let parent_expiry = parent_r.message.membership_expiry_date;
                    
                    if (!parent_plan) {
                        frappe.msgprint({
                            title: 'Error', 
                            indicator: 'red', 
                            message: 'The selected Parent Institution does not have an active Membership Plan.'
                        });
                        frm.set_value('parent_institution', '');
                        return;
                    }

                    // Inherit Plan and Dates from Parent Institution
                    frm.set_value('membership_plan', parent_plan);
                    if (parent_start) {
                        frm.set_value('membership_start_date', parent_start);
                        frm.set_value('original_join_date', parent_start);
                    }
                    if (parent_expiry) {
                        frm.set_value('membership_expiry_date', parent_expiry);
                        frm.set_df_property('membership_expiry_date', 'hidden', 0);
                    }

                    // Check Nominee Quota Limits
                    frappe.db.get_value('Item', parent_plan, 'custom_allowed_nominees').then(item_r => {
                        let max_limit = item_r.message.custom_allowed_nominees || 0;
                        frappe.call({
                            method: 'frappe.client.get_list',
                            args: {
                                doctype: 'Club Member',
                                filters: {
                                    'parent_institution': frm.doc.parent_institution,
                                    'status': ['!=', 'Inactive'], 
                                    'name': ['!=', frm.doc.name] 
                                },
                                fields: ['name']
                            },
                            callback: function(list_r) {
                                let active_nominees = list_r.message || [];
                                if (active_nominees.length >= max_limit) {
                                    frappe.msgprint({
                                        title: 'Limit Reached', 
                                        indicator: 'red', 
                                        message: `This institution has reached its limit of <b>${max_limit} active nominees</b>.`
                                    });
                                    frm.set_value('parent_institution', '');
                                    frm.set_value('naming_series', '');
                                    return;
                                }
                                let new_id = frm.doc.parent_institution + '-#';
                                frm.set_value('naming_series', new_id);
                            }
                        });
                    });
                }
            });
        }
    },

    membership_plan: function(frm) {
        if (!frm.is_new() || frm.doc.customer_group === 'Institutional Nominee') return;

        if (frm.doc.membership_plan) {
            frappe.db.get_value('Item', frm.doc.membership_plan, 'custom_validity_in_years').then(r => {
                let years = r.message.custom_validity_in_years || 0;
                if (years > 0) {
                    let start_date = frm.doc.membership_start_date || frappe.datetime.get_today();
                    let expiry_date = frappe.datetime.add_days(frappe.datetime.add_months(start_date, years * 12), -1);
                    
                    frm.set_value('membership_expiry_date', expiry_date);
                    
                    if (!frm.doc.membership_start_date) {
                        frm.set_value('membership_start_date', start_date);
                        frm.set_value('original_join_date', start_date);
                    }
                    
                    frm.set_df_property('membership_expiry_date', 'hidden', 0);
                    frm.set_df_property('membership_expiry_date', 'reqd', 1);
                } else {
                    frm.set_value('membership_expiry_date', '');
                    frm.set_df_property('membership_expiry_date', 'hidden', 1);
                }
            });
        }
    },

    membership_start_date: function(frm) {
        if (!frm.is_new() || frm.doc.customer_group === 'Institutional Nominee') return;

        if (frm.doc.membership_start_date) {
            frm.set_value('original_join_date', frm.doc.membership_start_date);
        }

        if (frm.doc.membership_plan) frm.trigger('membership_plan');
    },

    refresh: function(frm) {
        // --- LOCK-DOWN METHOD: Prevent name changes after initial save ---
        if (!frm.is_new()) {
            frm.set_df_property('member_name', 'read_only', 1);
        }

        // --- Existing Nominee Retirement Logic ---
        if (frm.doc.customer_group === 'Institutional Nominee' && frm.doc.status === 'Active' && !frm.is_new()) {
            frm.add_custom_button(__('Retire Nominee'), function() {
                frappe.confirm('Retire this nominee? This sets the status to Inactive and frees the institutional quota slot.', function() {
                    frm.set_value('status', 'Inactive');
                    frm.save().then(() => {
                        frappe.msgprint({ title: 'Success', indicator: 'green', message: 'Nominee Retired!' });
                    });
                });
            }).addClass('btn-danger');
        }
    }
});
