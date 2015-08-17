var old_custom_refresh = cur_frm.cscript.custom_refresh;

cur_frm.cscript.custom_refresh = function(doc, dt, dn) {
    if(doc.docstatus==0 && !doc.__islocal && !doc.fedex_shipment) {
        cur_frm.add_custom_button(__('Make Fedex Shipment'),
            cur_frm.cscript['Make Fedex Shipment'], frappe.boot.doctype_icons["Fedex Shipment"]);
    }
    if (old_custom_refresh) {
        old_custom_refresh(doc, dt, dn);
    }
}

cur_frm.cscript['Make Fedex Shipment'] = function() {
    frappe.model.open_mapped_doc({
        method: "fedex_shipment.shipment.make_fedex_shipment",
        frm: cur_frm
    })
}
