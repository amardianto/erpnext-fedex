cur_frm.cscript.recipient_address = function() {
    var me = this;
    frappe.call({
        freeze: true,
        method: "fedex_shipment.shipment.get_address_details",
        args: {
            "address": me.frm.doc.recipient_address,
        },
        callback: function(r) {
            if(!r.exc) {
                if(r.message) {
                    me.frm.set_value("recipient_address_address_line_1", r.message.address_line1);
                    me.frm.set_value("recipient_address_city", r.message.city);
                    me.frm.set_value("recipient_address_state_or_province_code", r.message.state_or_province_code);
                    me.frm.set_value("recipient_address_postal_code", r.message.pincode);
                    me.frm.set_value("recipient_address_country_code", r.message.country_code);
                    me.frm.set_value("recipient_contact_person_name", r.message.customer_name);
                    me.frm.set_value("recipient_contact_company_name", r.message.customer_name);
                    me.frm.set_value("recipient_contact_phone_number", r.message.phone);
                    me.frm.set_value("recipient_address_residential", r.message.residential);
                }
            }
        }
    });
}