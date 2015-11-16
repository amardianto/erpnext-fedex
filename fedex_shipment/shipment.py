# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import logging
import base64
import json
import StringIO

from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import frappe
from frappe.utils.file_manager import save_file, get_file, get_files_path
from frappe.utils import cstr, flt
from frappe.model.mapper import get_mapped_doc

from fedex.services.ship_service import FedexProcessShipmentRequest
from fedex.services.ship_service import FedexDeleteShipmentRequest
from fedex.services.track_service import FedexTrackRequest
from fedex.services.rate_service import FedexRateServiceRequest
from fedex.services.package_movement import PostalCodeInquiryRequest
from fedex.services.address_validation_service import FedexAddressValidationRequest

import fedex_config
import countries
import utils


# Set this to the INFO level to see the response from Fedex printed in stdout.
logging.basicConfig(level=logging.DEBUG)


PDF_CANVAS_SIZE_MAPPING = {
    "PAPER_4X6": (4 * inch, 6 * inch),
    "PAPER_4X8": (4 * inch, 8 * inch),
    "PAPER_4X9": (4 * inch, 9 * inch),
    "PAPER_7X4.75": (7 * inch, 4.75 * inch),
    "PAPER_8.5X11_BOTTOM_HALF_LABEL": (8.5 * inch, 11 * inch),
    "PAPER_8.5X11_TOP_HALF_LABEL": (8.5 * inch, 11 * inch),
    "STOCK_4X6": (4 * inch, 6 * inch),
    "STOCK_4X6.75_LEADING_DOC_TAB": (4 * inch, 6.75 * inch),
    "STOCK_4X6.75_TRAILING_DOC_TAB": (4 * inch, 6.75 * inch),
    "STOCK_4X8": (4 * inch, 8 * inch),
    "STOCK_4X9_LEADING_DOC_TAB": (4 * inch, 9 * inch),
    "STOCK_4X9_TRAILING_DOC_TAB": (4 * inch, 9 * inch),
    "PAPER \"6X4\"": (6 * inch, 4 * inch)
}


def validate(doc, method=None):
    pass


def before_submit(doc, method=None):
    create(doc)


def on_submit(doc, method=None):
    if not frappe.db.get_value('Packing Slip', doc.packing_slip, 'oc_tracking_number'):
        frappe.db.set_value('Packing Slip', doc.packing_slip, 'oc_tracking_number', doc.tracking_number)
        frappe.msgprint('Tracking number was updated for Packing Slip %s' % doc.packing_slip)
    else:
        frappe.msgprint('Cannot update tracking number for Packing Slip %s' % doc.packing_slip)
    frappe.db.set_value('Packing Slip', doc.get('delivery_note'), 'oc_tracking_number', doc.get('tracking_number'))

    delivery_note = frappe.db.get_value('Packing Slip', doc.packing_slip, 'delivery_note')
    if not frappe.db.get_value('Delivery Note', delivery_note, 'oc_tracking_number'):
        frappe.db.set_value('Delivery Note', delivery_note, 'oc_tracking_number', doc.tracking_number)
        frappe.msgprint('Tracking number was updated for Delivery Note %s' % delivery_note)
    else:
        frappe.msgprint('Cannot update tracking number for Delivery Note %s' % delivery_note)

    if not frappe.db.get_value('Packing Slip', doc.packing_slip, 'fedex_shipment'):
        frappe.db.set_value('Packing Slip', doc.packing_slip, 'fedex_shipment', doc.name)
    frappe.clear_cache()


def before_cancel(doc, method=None):
    delete(doc)
    frappe.db.set_value("Packing Slip", doc.packing_slip, "fedex_shipment", None)


def make_pdf_canvas(doc_fedex_shipment):
    if doc_fedex_shipment.label_image_type.lower() == "png":
        marging = 0.25
        width, height = PDF_CANVAS_SIZE_MAPPING.get('label_stock_type', (4 * inch, 6 * inch))
        c = canvas.Canvas('fedex_labels.pdf', pagesize=(width + marging * inch, height + marging * inch))
        return c, width, height, marging
    else:
        [None] * 4


def add_label_to_canvas(pdf_canvas, width, height, marging, label_image_data):
    if pdf_canvas:
        image_reader = ImageReader(StringIO.StringIO(label_image_data))
        pdf_canvas.drawImage(image_reader, marging, marging, width, height, preserveAspectRatio=True)
        pdf_canvas.showPage()


def get_customer_references(doc_fedex_shipment):
    if not doc_fedex_shipment.packing_slip:
        frappe.throw("Please specify Packing Slip in Fedex Shipment")
    dn = frappe.db.get_value("Packing Slip", doc_fedex_shipment.packing_slip, "delivery_note")
    po_no = frappe.db.get_value("Delivery Note", dn, "po_no")
    return (dn, po_no)


def update_customer_references(doc_fedex_shipment, shipment, package):
    dn_name, po_no = get_customer_references(doc_fedex_shipment)
    if dn_name:
        customer_reference = shipment.create_wsdl_object_of_type('CustomerReference')
        customer_reference.CustomerReferenceType = "INVOICE_NUMBER"
        customer_reference.Value = dn_name
        package.CustomerReferences.append(customer_reference)
    if po_no:
        customer_reference = shipment.create_wsdl_object_of_type('CustomerReference')
        customer_reference.CustomerReferenceType = "P_O_NUMBER"
        customer_reference.Value = po_no
        package.CustomerReferences.append(customer_reference)


def create(doc_fedex_shipment):
    # init stuff
    pdf_canvas, canvas_img_width, canvas_img_height, canvas_img_marging = make_pdf_canvas(doc_fedex_shipment)
    config_obj = fedex_config.get(doc_fedex_shipment.fedex_settings)

    # This is the object that will be handling our tracking request.
    shipment = FedexProcessShipmentRequest(config_obj)

    # This is very generalized, top-level information.
    # REGULAR_PICKUP, REQUEST_COURIER, DROP_BOX, BUSINESS_SERVICE_CENTER or STATION
    shipment.RequestedShipment.DropoffType = doc_fedex_shipment.drop_off_type

    # See page 355 in WS_ShipService.pdf for a full list. Here are the common ones:
    # STANDARD_OVERNIGHT, PRIORITY_OVERNIGHT, FEDEX_GROUND, FEDEX_EXPRESS_SAVER
    shipment.RequestedShipment.ServiceType = doc_fedex_shipment.service_type

    # What kind of package this will be shipped in.
    # FEDEX_BOX, FEDEX_PAK, FEDEX_TUBE, YOUR_PACKAGING
    shipment.RequestedShipment.PackagingType = doc_fedex_shipment.packaging_type

    # Shipper address.
    shipment.RequestedShipment.Shipper.Address.CountryCode = doc_fedex_shipment.shipper_address_country_code
    shipment.RequestedShipment.Shipper.Address.PostalCode = doc_fedex_shipment.shipper_address_postal_code
    shipment.RequestedShipment.Shipper.Address.StateOrProvinceCode = doc_fedex_shipment.shipper_address_state_or_province_code
    shipment.RequestedShipment.Shipper.Address.City = doc_fedex_shipment.shipper_address_city
    shipment.RequestedShipment.Shipper.Address.StreetLines = [doc_fedex_shipment.shipper_address_address_line_1]
    shipment.RequestedShipment.Shipper.Address.Residential = True if doc_fedex_shipment.shipper_address_residential else False

    # Shipper contact info.
    shipment.RequestedShipment.Shipper.Contact.PersonName = doc_fedex_shipment.shipper_contact_person_name
    shipment.RequestedShipment.Shipper.Contact.CompanyName = doc_fedex_shipment.shipper_contact_company_name
    shipment.RequestedShipment.Shipper.Contact.PhoneNumber = doc_fedex_shipment.shipper_contact_phone_number

    # Recipient address
    # This is needed to ensure an accurate rate quote with the response.
    shipment.RequestedShipment.Recipient.Address.CountryCode = doc_fedex_shipment.recipient_address_country_code
    shipment.RequestedShipment.Recipient.Address.PostalCode = doc_fedex_shipment.recipient_address_postal_code
    shipment.RequestedShipment.Recipient.Address.StateOrProvinceCode = doc_fedex_shipment.recipient_address_state_or_province_code
    shipment.RequestedShipment.Recipient.Address.City = doc_fedex_shipment.recipient_address_city
    shipment.RequestedShipment.Recipient.Address.StreetLines = [doc_fedex_shipment.recipient_address_address_line_1]
    shipment.RequestedShipment.Recipient.Address.Residential = True if doc_fedex_shipment.recipient_address_residential else False

    # Recipient contact info.
    shipment.RequestedShipment.Recipient.Contact.PersonName = doc_fedex_shipment.recipient_contact_person_name
    shipment.RequestedShipment.Recipient.Contact.CompanyName = doc_fedex_shipment.recipient_contact_company_name
    shipment.RequestedShipment.Recipient.Contact.PhoneNumber = doc_fedex_shipment.recipient_contact_phone_number

    shipment.RequestedShipment.EdtRequestType = 'NONE'

    shipment.RequestedShipment.ShippingChargesPayment.Payor.ResponsibleParty.AccountNumber = config_obj.account_number
    # Who pays for the shipment?
    # RECIPIENT, SENDER or THIRD_PARTY
    shipment.RequestedShipment.ShippingChargesPayment.PaymentType = doc_fedex_shipment.payment_type
    shipment.RequestedShipment.PreferredCurrency = doc_fedex_shipment.preferred_currency

    # Specifies the label type to be returned.
    # LABEL_DATA_ONLY or COMMON2D
    shipment.RequestedShipment.LabelSpecification.LabelFormatType = doc_fedex_shipment.label_format_type

    # Specifies which format the label file will be sent to you in.
    # DPL, EPL2, PDF, PNG, ZPLII
    shipment.RequestedShipment.LabelSpecification.ImageType = doc_fedex_shipment.label_image_type

    # To use doctab stocks, you must change ImageType above to one of the
    # label printer formats (ZPLII, EPL2, DPL).
    # See documentation for paper types, there quite a few.
    shipment.RequestedShipment.LabelSpecification.LabelStockType = doc_fedex_shipment.label_stock_type

    # This indicates if the top or bottom of the label comes out of the
    # printer first.
    # BOTTOM_EDGE_OF_TEXT_FIRST or TOP_EDGE_OF_TEXT_FIRST
    shipment.RequestedShipment.LabelSpecification.LabelPrintingOrientation = doc_fedex_shipment.label_printing_orientation

    doc_master_package = doc_fedex_shipment.packages[0]
    package = shipment.create_wsdl_object_of_type('RequestedPackageLineItem')
    package.PhysicalPackaging = 'BOX'
    update_customer_references(doc_fedex_shipment, shipment, package)

    # adding weight
    package_weight = shipment.create_wsdl_object_of_type('Weight')
    package_weight.Units = doc_master_package.weight_units
    package_weight.Value = doc_master_package.weight_value
    package.Weight = package_weight

    # adding dimensions
    package_dimensions = shipment.create_wsdl_object_of_type('Dimensions')
    package_dimensions.Units = doc_master_package.dimensions_units
    package_dimensions.Length = doc_master_package.length
    package_dimensions.Width = doc_master_package.width
    package_dimensions.Height = doc_master_package.height
    package.Dimensions = package_dimensions

    package.SequenceNumber = 1
    shipment.RequestedShipment.RequestedPackageLineItems = [package]
    shipment.RequestedShipment.PackageCount = len(doc_fedex_shipment.packages)
    shipment.RequestedShipment.TotalWeight.Units = doc_fedex_shipment.packages[0].weight_units
    shipment.RequestedShipment.TotalWeight.Value = sum(flt(p.weight_value) for p in doc_fedex_shipment.packages)

    # shipment.RequestedShipment.CustomsClearanceDetail = shipment.create_wsdl_object_of_type('CustomsClearanceDetail')
    # shipment.RequestedShipment.CustomsClearanceDetail.DutiesPayment = new Payment();
    # shipment.RequestedShipment.CustomsClearanceDetail.DutiesPayment.PaymentType = PaymentType.SENDER;
    # shipment.RequestedShipment.CustomsClearanceDetail.DutiesPayment.Payor = new Payor();
    # shipment.RequestedShipment.CustomsClearanceDetail.DutiesPayment.Payor.AccountNumber = "XXX"; // Replace "XXX" with the payor account number
    # shipment.RequestedShipment.CustomsClearanceDetail.DutiesPayment.Payor.CountryCode = "CA";
    # shipment.RequestedShipment.CustomsClearanceDetail.DocumentContent = InternationalDocumentContentType.NON_DOCUMENTS;

    # shipment.RequestedShipment.CustomsClearanceDetail.CustomsValue = new Money();
    # shipment.RequestedShipment.CustomsClearanceDetail.CustomsValue.Amount = 100.0M;
    # shipment.RequestedShipment.CustomsClearanceDetail.CustomsValue.Currency = "USD";

    # shipment.RequestedShipment.CustomsClearanceDetail.CustomsValue.Currency = 'USD'
    # shipment.RequestedShipment.CustomsClearanceDetail.CustomsValue.Amount = '123.4'

    # If you'd like to see some documentation on the ship service WSDL, un-comment
    # this line. (Spammy).
    # print shipment.client

    # Un-comment this to see your complete, ready-to-send request as it stands
    # before it is actually sent. This is useful for seeing what values you can
    # change.
    # print shipment.RequestedShipment

    # If you want to make sure that all of your entered details are valid, you
    # can call this and parse it just like you would via send_request(). If
    # shipment.response.HighestSeverity == "SUCCESS", your shipment is valid.
    # shipment.send_validation_request()

    # Fires off the request, sets the 'response' attribute on the object.
    try:
        shipment.send_request()
    except Exception as ex:
        frappe.throw('Fedex API: ' + cstr(ex))
    # frappe.msgprint('11111---' * 100 + cstr(shipment.response))
    # This will show the reply to your shipment being sent. You can access the
    # attributes through the response attribute on the request object. This is
    # good to un-comment to see the variables returned by the Fedex reply.
    # print shipment.response

    # SUCCESS — Your transaction succeeded with no other applicable information.
    # NOTE — Additional information that may be of interest to you about your transaction.
    # WARNING — Additional information that you need to know about your transaction that you may need to take action on.
    # ERROR — Information about an error that occurred while processing your transaction.
    # FAILURE — FedEx was unable to process your transaction.
    msg = ''
    try:
        msg = shipment.response.Message
    except:
        pass
    if shipment.response.HighestSeverity == "SUCCESS":
        frappe.msgprint('Shipment is created successfully in Fedex service.')
    elif shipment.response.HighestSeverity == "NOTE":
        frappe.msgprint('Shipment is created in Fedex service with the following note:\n%s' % msg)
        for notification in shipment.response.Notifications:
            frappe.msgprint('Code: %s, %s' % (notification.Code, notification.Message))
    elif shipment.response.HighestSeverity == "WARNING":
        frappe.msgprint('Shipment is created in Fedex service with the following warning:\n%s' % msg)
        for notification in shipment.response.Notifications:
            frappe.msgprint('Code: %s, %s' % (notification.Code, notification.Message))
    else:  # ERROR, FAILURE
        frappe.throw('Creating of Shipment in Fedex service failed.')
        for notification in shipment.response.Notifications:
            frappe.msgprint('Code: %s, %s' % (notification.Code, notification.Message))

#########
    # for service in rate_request.response.RateReplyDetails:
    #     for detail in service.RatedShipmentDetails:
    #         for surcharge in detail.ShipmentRateDetail.Surcharges:
    #             if surcharge.SurchargeType == 'OUT_OF_DELIVERY_AREA':
    #                 print "%s: ODA rate_request charge %s" % (service.ServiceType, surcharge.Amount.Amount)

    #     for rate_detail in service.RatedShipmentDetails:
    #         print "%s: Net FedEx Charge %s %s" % (service.ServiceType, rate_detail.ShipmentRateDetail.TotalNetFedExCharge.Currency, rate_detail.ShipmentRateDetail.TotalNetFedExCharge.Amount)
#########

    # Net shipping costs.
    # print "Net Shipping Cost (US$):", shipment.response.CompletedShipmentDetail.CompletedPackageDetails[0].PackageRating.PackageRateDetails[0].NetCharge.Amount

    master_tracking_number = shipment.response.CompletedShipmentDetail.CompletedPackageDetails[0].TrackingIds[0].TrackingNumber
    label_image_data = base64.b64decode(shipment.response.CompletedShipmentDetail.CompletedPackageDetails[0].Label.Parts[0].Image)
    saved_file = save_file('fedex_label_%s.%s' % (master_tracking_number, doc_fedex_shipment.label_image_type.lower()), label_image_data, doc_fedex_shipment.doctype, doc_fedex_shipment.name)
    add_label_to_canvas(pdf_canvas, canvas_img_width, canvas_img_height, canvas_img_marging, label_image_data)

    doc_master_package.update({
        'tracking_number': master_tracking_number,
        'label_image': saved_file.file_url
    })

    doc_fedex_shipment.update({
        'tracking_number': master_tracking_number,
        'label_image': saved_file.file_url,
        # 'status': shipment.response.HighestSeverity,
        'total_net_charge': shipment.response.HighestSeverity,
        'total_net_fedex_charge': shipment.response.HighestSeverity,
        'total_taxes': shipment.response.HighestSeverity,
        'total_base_charge': shipment.response.HighestSeverity,
        'total_net_freight': shipment.response.HighestSeverity,
        'total_surcharges': shipment.response.HighestSeverity,
        'total_rebates': shipment.response.HighestSeverity,
        'total_freight_discounts': shipment.response.HighestSeverity,
        'raw_response': cstr(shipment.response)
    })

    try:
        if len(doc_fedex_shipment.packages) > 1:
            shipment.RequestedShipment.MasterTrackingId.TrackingNumber = master_tracking_number
            shipment.RequestedShipment.MasterTrackingId.TrackingIdType.value = 'EXPRESS'
            for i, doc_package in enumerate(doc_fedex_shipment.packages[1:]):
                package = shipment.create_wsdl_object_of_type('RequestedPackageLineItem')
                package.PhysicalPackaging = 'BOX'
                update_customer_references(doc_fedex_shipment, shipment, package)

                # adding weight
                package_weight = shipment.create_wsdl_object_of_type('Weight')
                package_weight.Units = doc_package.weight_units
                package_weight.Value = doc_package.weight_value
                package.Weight = package_weight

                # adding dimensions
                package_dimensions = shipment.create_wsdl_object_of_type('Dimensions')
                package_dimensions.Units = doc_package.dimensions_units
                package_dimensions.Length = doc_package.length
                package_dimensions.Width = doc_package.width
                package_dimensions.Height = doc_package.height
                package.Dimensions = package_dimensions

                package.SequenceNumber = i + 2
                shipment.RequestedShipment.RequestedPackageLineItems = [package]
                shipment.RequestedShipment.PackageCount = len(doc_fedex_shipment.packages)
                shipment.send_request()

                msg = ''
                try:
                    msg = shipment.response.Message
                except:
                    pass
                if shipment.response.HighestSeverity == "SUCCESS":
                    frappe.msgprint('Shipment package is added successfully.')
                elif shipment.response.HighestSeverity == "NOTE":
                    frappe.msgprint('Shipment package is added with the following note:\n%s' % msg)
                    for notification in shipment.response.Notifications:
                        frappe.msgprint('Code: %s, %s' % (notification.Code, notification.Message))
                elif shipment.response.HighestSeverity == "WARNING":
                    frappe.msgprint('Shipment package is added with the following warning:\n%s' % msg)
                    for notification in shipment.response.Notifications:
                        frappe.msgprint('Code: %s, %s' % (notification.Code, notification.Message))
                else:  # ERROR, FAILURE
                    frappe.throw('Adding of Shipment package is failed.')
                    for notification in shipment.response.Notifications:
                        frappe.msgprint('Code: %s, %s' % (notification.Code, notification.Message))

                # updating shipment package items
                tracking_number = shipment.response.CompletedShipmentDetail.CompletedPackageDetails[0].TrackingIds[0].TrackingNumber
                label_image_data = base64.b64decode(shipment.response.CompletedShipmentDetail.CompletedPackageDetails[0].Label.Parts[0].Image)
                saved_file = save_file('fedex_label_%s.%s' % (tracking_number, doc_fedex_shipment.label_image_type.lower()), label_image_data, doc_fedex_shipment.doctype, doc_fedex_shipment.name)
                doc_package.update({
                    'tracking_number': tracking_number,
                    'label_image': saved_file.file_url
                })
                add_label_to_canvas(pdf_canvas, canvas_img_width, canvas_img_height, canvas_img_marging, label_image_data)

        # complete pdf doc
        try:
            pdf_canvas and save_file('all_fedex_labels_%s.pdf' % master_tracking_number, pdf_canvas.getpdfdata(), doc_fedex_shipment.doctype, doc_fedex_shipment.name)
        except Exception as ex:
            frappe.msgprint('Cannot merge Fedex labels to PDF file:\n' + cstr(ex))
    except Exception as ex:
        delete(doc_fedex_shipment)
        frappe.throw(cstr(ex))
    try:
        for shipment_rate_detail in shipment.response.CompletedShipmentDetail.ShipmentRating.ShipmentRateDetails:
            if shipment_rate_detail.RateType == shipment.response.CompletedShipmentDetail.ShipmentRating.ActualRateType:
                doc_fedex_shipment.update({
                    'total_net_charge': flt(shipment_rate_detail.TotalNetCharge.Amount),
                    'totals_currency': cstr(shipment_rate_detail.TotalNetCharge.Currency)
                })
                break
    except Exception as ex:
        frappe.msgprint('Cannot update Total Amounts: %s' % cstr(ex))

    # del_request.TrackingId.TrackingIdType = 'EXPRESS'
        # for i, doc_package in enumerate(doc_fedex_shipment.packages):

        #     # Getting the tracking number from the new shipment.
        #     tracking_number = shipment.response.CompletedShipmentDetail.CompletedPackageDetails[i].TrackingIds[0].TrackingNumber
        #     # frappe.msgprint(str(shipment.response))
        #     # Net shipping costs.
        #     # print "Net Shipping Cost (US$):", shipment.response.CompletedShipmentDetail.CompletedPackageDetails[0].PackageRating.PackageRateDetails[0].NetCharge.Amount

        #     label_image_data = base64.b64decode(shipment.response.CompletedShipmentDetail.CompletedPackageDetails[i].Label.Parts[0].Image)
        #     saved_file = save_file('fedex_label_%s.%s' % (tracking_number, doc_fedex_shipment.label_image_type.lower()), label_image_data, doc_fedex_shipment.doctype, doc_fedex_shipment.name)

        #     doc_package.update({
        #         'tracking_number': tracking_number,
        #         'label_image': saved_file.file_url
        #     })
        #     doc_package.save()

    """
    This is an example of how to dump a label to a PNG file.
    """
    # This will be the file we write the label out to.
    # png_file = open('example_shipment_label.png', 'wb')
    # png_file.write(label_binary_data)
    # png_file.close()

    """
    This is an example of how to print the label to a serial printer. This will not
    work for all label printers, consult your printer's documentation for more
    details on what formats it can accept.
    """
    # Pipe the binary directly to the label printer. Works under Linux
    # without requiring PySerial. This WILL NOT work on other platforms.
    # label_printer = open("/dev/ttyS0", "w")
    # label_printer.write(label_binary_data)
    # label_printer.close()

    """
    This is a potential cross-platform solution using pySerial. This has not been
    tested in a long time and may or may not work. For Windows, Mac, and other
    platforms, you may want to go this route.
    """
    # import serial
    # label_printer = serial.Serial(0)
    # print "SELECTED SERIAL PORT: "+ label_printer.portstr
    # label_printer.write(label_binary_data)
    # label_printer.close()
    frappe.clear_cache()


def create_freight(doc_fedex_shipment):
    config_obj = fedex_config.get(doc_fedex_shipment.fedex_settings)

    # This is the object that will be handling our tracking request.
    shipment = FedexProcessShipmentRequest(config_obj)
    shipment.RequestedShipment.DropoffType = 'REGULAR_PICKUP'
    shipment.RequestedShipment.ServiceType = 'FEDEX_FREIGHT_ECONOMY'
    shipment.RequestedShipment.PackagingType = 'YOUR_PACKAGING'

    shipment.RequestedShipment.FreightShipmentDetail.FedExFreightAccountNumber = config_obj.freight_account_number

    # Shipper contact info.
    shipment.RequestedShipment.Shipper.Contact.PersonName = 'Sender Name'
    shipment.RequestedShipment.Shipper.Contact.CompanyName = 'Some Company'
    shipment.RequestedShipment.Shipper.Contact.PhoneNumber = '9012638716'

    # Shipper address.
    shipment.RequestedShipment.Shipper.Address.StreetLines = ['1202 Chalet Ln']
    shipment.RequestedShipment.Shipper.Address.City = 'Harrison'
    shipment.RequestedShipment.Shipper.Address.StateOrProvinceCode = 'AR'
    shipment.RequestedShipment.Shipper.Address.PostalCode = '72601'
    shipment.RequestedShipment.Shipper.Address.CountryCode = 'US'
    shipment.RequestedShipment.Shipper.Address.Residential = True

    # Recipient contact info.
    shipment.RequestedShipment.Recipient.Contact.PersonName = 'Recipient Name'
    shipment.RequestedShipment.Recipient.Contact.CompanyName = 'Recipient Company'
    shipment.RequestedShipment.Recipient.Contact.PhoneNumber = '9012637906'

    # Recipient address
    shipment.RequestedShipment.Recipient.Address.StreetLines = ['2000 Freight LTL Testing']
    shipment.RequestedShipment.Recipient.Address.City = 'Harrison'
    shipment.RequestedShipment.Recipient.Address.StateOrProvinceCode = 'AR'
    shipment.RequestedShipment.Recipient.Address.PostalCode = '72601'
    shipment.RequestedShipment.Recipient.Address.CountryCode = 'US'

    # This is needed to ensure an accurate rate quote with the response.
    shipment.RequestedShipment.Recipient.Address.Residential = False
    shipment.RequestedShipment.FreightShipmentDetail.TotalHandlingUnits = 1
    shipment.RequestedShipment.ShippingChargesPayment.Payor.ResponsibleParty.AccountNumber = config_obj.freight_account_number

    shipment.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Contact.PersonName = 'Sender Name'
    shipment.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Contact.CompanyName = 'Some Company'
    shipment.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Contact.PhoneNumber = '9012638716'

    shipment.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.StreetLines = ['2000 Freight LTL Testing']
    shipment.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.City = 'Harrison'
    shipment.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.StateOrProvinceCode = 'AR'
    shipment.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.PostalCode = '72601'
    shipment.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.CountryCode = 'US'
    shipment.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.Residential = False
    spec = shipment.create_wsdl_object_of_type('ShippingDocumentSpecification')

    spec.ShippingDocumentTypes = [spec.CertificateOfOrigin]
    # shipment.RequestedShipment.ShippingDocumentSpecification = spec

    role = shipment.create_wsdl_object_of_type('FreightShipmentRoleType')

    shipment.RequestedShipment.FreightShipmentDetail.Role = role.SHIPPER
    shipment.RequestedShipment.FreightShipmentDetail.CollectTermsType = 'STANDARD'

    # Specifies the label type to be returned.
    shipment.RequestedShipment.LabelSpecification.LabelFormatType = 'FEDEX_FREIGHT_STRAIGHT_BILL_OF_LADING'

    # Specifies which format the label file will be sent to you in.
    # DPL, EPL2, PDF, PNG, ZPLII
    shipment.RequestedShipment.LabelSpecification.ImageType = 'PDF'

    # To use doctab stocks, you must change ImageType above to one of the
    # label printer formats (ZPLII, EPL2, DPL).
    # See documentation for paper types, there quite a few.
    shipment.RequestedShipment.LabelSpecification.LabelStockType = 'PAPER_LETTER'

    # This indicates if the top or bottom of the label comes out of the
    # printer first.
    # BOTTOM_EDGE_OF_TEXT_FIRST or TOP_EDGE_OF_TEXT_FIRST
    shipment.RequestedShipment.LabelSpecification.LabelPrintingOrientation = 'BOTTOM_EDGE_OF_TEXT_FIRST'
    shipment.RequestedShipment.EdtRequestType = 'NONE'

    package1_weight = shipment.create_wsdl_object_of_type('Weight')
    package1_weight.Value = 500.0
    package1_weight.Units = "LB"

    shipment.RequestedShipment.FreightShipmentDetail.PalletWeight = package1_weight

    package1 = shipment.create_wsdl_object_of_type('FreightShipmentLineItem')
    package1.Weight = package1_weight
    package1.Packaging = 'PALLET'
    package1.Description = 'Products'
    package1.FreightClass = 'CLASS_500'
    package1.HazardousMaterials = None
    package1.Pieces = 12

    shipment.RequestedShipment.FreightShipmentDetail.LineItems = package1

    # If you'd like to see some documentation on the ship service WSDL, un-comment
    # this line. (Spammy).
    # print shipment.client

    # Un-comment this to see your complete, ready-to-send request as it stands
    # before it is actually sent. This is useful for seeing what values you can
    # change.
    # print shipment.RequestedShipment

    # If you want to make sure that all of your entered details are valid, you
    # can call this and parse it just like you would via send_request(). If
    # shipment.response.HighestSeverity == "SUCCESS", your shipment is valid.
    # shipment.send_validation_request()

    # Fires off the request, sets the 'response' attribute on the object.
    shipment.send_request()

    # This will show the reply to your shipment being sent. You can access the
    # attributes through the response attribute on the request object. This is
    # good to un-comment to see the variables returned by the Fedex reply.
    print shipment.response
    # Here is the overall end result of the query.
    # print "HighestSeverity:", shipment.response.HighestSeverity
    # # Getting the tracking number from the new shipment.
    # print "Tracking #:", shipment.response.CompletedShipmentDetail.CompletedPackageDetails[0].TrackingIds[0].TrackingNumber
    # # Net shipping costs.
    # print "Net Shipping Cost (US$):", shipment.response.CompletedShipmentDetail.CompletedPackageDetails[0].PackageRating.PackageRateDetails[0].NetCharge.Amount

    # # Get the label image in ASCII format from the reply. Note the list indices
    # we're using. You'll need to adjust or iterate through these if your shipment
    # has multiple packages.

    ascii_label_data = shipment.response.CompletedShipmentDetail.ShipmentDocuments[0].Parts[0].Image

    # Convert the ASCII data to binary.
    label_binary_data = binascii.a2b_base64(ascii_label_data)

    """
    This is an example of how to dump a label to a PNG file.
    """
    # This will be the file we write the label out to.
    pdf_file = open('example_shipment_label.pdf', 'wb')
    pdf_file.write(label_binary_data)
    pdf_file.close()

    """
    This is an example of how to print the label to a serial printer. This will not
    work for all label printers, consult your printer's documentation for more
    details on what formats it can accept.
    """
    # Pipe the binary directly to the label printer. Works under Linux
    # without requiring PySerial. This WILL NOT work on other platforms.
    # label_printer = open("/dev/ttyS0", "w")
    # label_printer.write(label_binary_data)
    # label_printer.close()

    """
    This is a potential cross-platform solution using pySerial. This has not been
    tested in a long time and may or may not work. For Windows, Mac, and other
    platforms, you may want to go this route.
    """
    # import serial
    # label_printer = serial.Serial(0)
    # print "SELECTED SERIAL PORT: "+ label_printer.portstr
    # label_printer.write(label_binary_data)
    # label_printer.close()


def delete(doc_fedex_shipment):
    config_obj = fedex_config.get(doc_fedex_shipment.fedex_settings)

    # This is the object that will be handling our tracking request.
    del_request = FedexDeleteShipmentRequest(config_obj)

    # Either delete all packages in a shipment, or delete an individual package.
    # Docs say this isn't required, but the WSDL won't validate without it.
    # DELETE_ALL_PACKAGES, DELETE_ONE_PACKAGE
    del_request.DeletionControlType = "DELETE_ALL_PACKAGES"

    # The tracking number of the shipment to delete.
    del_request.TrackingId.TrackingNumber = doc_fedex_shipment.tracking_number

    # What kind of shipment the tracking number used.
    # Docs say this isn't required, but the WSDL won't validate without it.
    # EXPRESS, GROUND, or USPS
    del_request.TrackingId.TrackingIdType = 'EXPRESS'

    # Fires off the request, sets the 'response' attribute on the object.
    try:
        del_request.send_request()
    except Exception as ex:
        frappe.throw('Fedex API: ' + cstr(ex))

    # See the response printed out.
    if del_request.response.HighestSeverity == "SUCCESS":
        frappe.msgprint('Shipment with tracking number %s is deleted successfully.' % doc_fedex_shipment.tracking_number)
    else:
        codes = []
        for notification in del_request.response.Notifications:
            codes.append(cstr(notification.Code))
            frappe.msgprint('Code: %s, %s' % (notification.Code, notification.Message))
        # code 8159, Shipment Delete was requested for a tracking number already in a deleted state.
        # code 8149, Unable to retrieve record from database
        if not ("8159" in codes or "8149" in codes):
            frappe.throw('Canceling of Shipment in Fedex service failed.')


def track(fedex_settings, track_number):
    config_obj = fedex_config.get(fedex_settings)

    # NOTE: TRACKING IS VERY ERRATIC ON THE TEST SERVERS. YOU MAY NEED TO USE
    # PRODUCTION KEYS/PASSWORDS/ACCOUNT #.
    track = FedexTrackRequest(config_obj)
    track.TrackPackageIdentifier.Type = 'TRACKING_NUMBER_OR_DOORTAG'
    track.TrackPackageIdentifier.Value = track_number

    # Fires off the request, sets the 'response' attribute on the object.
    track.send_request()

    # See the response printed out.
    print track.response

    # Look through the matches (there should only be one for a tracking number
    # query), and show a few details about each shipment.
    print "== Results =="
    for match in track.response.TrackDetails:
        print "Tracking #:", match.TrackingNumber
        print "Status:", match.StatusDescription


def rate_request(doc_fedex_shipment):
    config_obj = fedex_config.get(doc_fedex_shipment.fedex_settings)

    # This is the object that will be handling our tracking request.
    rate_request = FedexRateServiceRequest(config_obj)

    # If you wish to have transit data returned with your request you
    # need to uncomment the following
    # rate_request.ReturnTransitAndCommit = True

    # This is very generalized, top-level information.
    # REGULAR_PICKUP, REQUEST_COURIER, DROP_BOX, BUSINESS_SERVICE_CENTER or STATION
    rate_request.RequestedShipment.DropoffType = doc_fedex_shipment.drop_off_type

    # See page 355 in WS_ShipService.pdf for a full list. Here are the common ones:
    # STANDARD_OVERNIGHT, PRIORITY_OVERNIGHT, FEDEX_GROUND, FEDEX_EXPRESS_SAVER
    # To receive rates for multiple ServiceTypes set to None.
    rate_request.RequestedShipment.ServiceType = doc_fedex_shipment.service_type

    # What kind of package this will be shipped in.
    # FEDEX_BOX, FEDEX_PAK, FEDEX_TUBE, YOUR_PACKAGING
    rate_request.RequestedShipment.PackagingType = doc_fedex_shipment.packaging_type

    # Shipper's address
    rate_request.RequestedShipment.Shipper.Address.PostalCode = '29631'
    rate_request.RequestedShipment.Shipper.Address.CountryCode = 'US'
    rate_request.RequestedShipment.Shipper.Address.Residential = False

    # Recipient address
    rate_request.RequestedShipment.Recipient.Address.PostalCode = '27577'
    rate_request.RequestedShipment.Recipient.Address.CountryCode = 'US'
    # This is needed to ensure an accurate rate quote with the response.
    # rate_request.RequestedShipment.Recipient.Address.Residential = True
    # include estimated duties and taxes in rate quote, can be ALL or NONE
    rate_request.RequestedShipment.EdtRequestType = 'NONE'

    # Who pays for the rate_request?
    # RECIPIENT, SENDER or THIRD_PARTY
    rate_request.RequestedShipment.ShippingChargesPayment.PaymentType = 'SENDER'

    package1_weight = rate_request.create_wsdl_object_of_type('Weight')
    # Weight, in LB.
    package1_weight.Value = 1.0
    package1_weight.Units = "LB"

    package1 = rate_request.create_wsdl_object_of_type('RequestedPackageLineItem')
    package1.Weight = package1_weight
    # can be other values this is probably the most common
    package1.PhysicalPackaging = 'BOX'
    # Required, but according to FedEx docs:
    # "Used only with PACKAGE_GROUPS, as a count of packages within a
    # group of identical packages". In practice you can use this to get rates
    # for a shipment with multiple packages of an identical package size/weight
    # on rate request without creating multiple RequestedPackageLineItem elements.
    # You can OPTIONALLY specify a package group:
    # package1.GroupNumber = 0  # default is 0
    # The result will be found in RatedPackageDetail, with specified GroupNumber.
    package1.GroupPackageCount = 1
    # Un-comment this to see the other variables you may set on a package.
    # print package1

    # This adds the RequestedPackageLineItem WSDL object to the rate_request. It
    # increments the package count and total weight of the rate_request for you.
    rate_request.add_package(package1)

    # If you'd like to see some documentation on the ship service WSDL, un-comment
    # this line. (Spammy).
    # print rate_request.client

    # Un-comment this to see your complete, ready-to-send request as it stands
    # before it is actually sent. This is useful for seeing what values you can
    # change.
    # print rate_request.RequestedShipment

    # Fires off the request, sets the 'response' attribute on the object.
    rate_request.send_request()

    # This will show the reply to your rate_request being sent. You can access the
    # attributes through the response attribute on the request object. This is
    # good to un-comment to see the variables returned by the FedEx reply.
    # print rate_request.response

    # Here is the overall end result of the query.
    print "HighestSeverity:", rate_request.response.HighestSeverity

    # RateReplyDetails can contain rates for multiple ServiceTypes if ServiceType was set to None
    for service in rate_request.response.RateReplyDetails:
        for detail in service.RatedShipmentDetails:
            for surcharge in detail.ShipmentRateDetail.Surcharges:
                if surcharge.SurchargeType == 'OUT_OF_DELIVERY_AREA':
                    print "%s: ODA rate_request charge %s" % (service.ServiceType, surcharge.Amount.Amount)

        for rate_detail in service.RatedShipmentDetails:
            print "%s: Net FedEx Charge %s %s" % (service.ServiceType, rate_detail.ShipmentRateDetail.TotalNetFedExCharge.Currency, rate_detail.ShipmentRateDetail.TotalNetFedExCharge.Amount)


def freight_rate_request(doc_fedex_shipment):
    config_obj = fedex_config.get(doc_fedex_shipment.fedex_settings)

    # This is the object that will be handling our tracking request.
    rate_request = FedexRateServiceRequest(config_obj)

    rate_request.RequestedShipment.ServiceType = 'FEDEX_FREIGHT_ECONOMY'

    rate_request.RequestedShipment.DropoffType = 'REGULAR_PICKUP'

    rate_request.RequestedShipment.PackagingType = 'YOUR_PACKAGING'

    rate_request.RequestedShipment.FreightShipmentDetail.TotalHandlingUnits = 1

    rate_request.RequestedShipment.FreightShipmentDetail.FedExFreightAccountNumber = config_obj.freight_account_number

    rate_request.RequestedShipment.Shipper.Address.PostalCode = '72601'
    rate_request.RequestedShipment.Shipper.Address.CountryCode = 'US'
    rate_request.RequestedShipment.Shipper.Address.City = 'Harrison'
    rate_request.RequestedShipment.Shipper.Address.StateOrProvinceCode = 'AR'
    rate_request.RequestedShipment.Shipper.Address.Residential = False
    rate_request.RequestedShipment.Recipient.Address.PostalCode = '72601'
    rate_request.RequestedShipment.Recipient.Address.CountryCode = 'US'
    rate_request.RequestedShipment.Recipient.Address.StateOrProvinceCode = 'AR'
    rate_request.RequestedShipment.Recipient.Address.City = 'Harrison'

    # include estimated duties and taxes in rate quote, can be ALL or NONE
    rate_request.RequestedShipment.EdtRequestType = 'NONE'

    # note: in order for this to work in test, you may need to use the
    # specially provided LTL addresses emailed to you when signing up.
    rate_request.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Contact.PersonName = 'Sender Name'
    rate_request.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Contact.CompanyName = 'Some Company'
    rate_request.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Contact.PhoneNumber = '9012638716'
    rate_request.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.StreetLines = ['2000 Freight LTL Testing']
    rate_request.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.City = 'Harrison'
    rate_request.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.StateOrProvinceCode = 'AR'
    rate_request.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.PostalCode = '72601'
    rate_request.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.CountryCode = 'US'
    rate_request.RequestedShipment.FreightShipmentDetail.FedExFreightBillingContactAndAddress.Address.Residential = False

    spec = rate_request.create_wsdl_object_of_type('ShippingDocumentSpecification')

    spec.ShippingDocumentTypes = [spec.CertificateOfOrigin]

    rate_request.RequestedShipment.ShippingDocumentSpecification = spec

    role = rate_request.create_wsdl_object_of_type('FreightShipmentRoleType')
    rate_request.RequestedShipment.FreightShipmentDetail.Role = role.SHIPPER

    # Designates the terms of the "collect" payment for a Freight
    # Shipment. Can be NON_RECOURSE_SHIPPER_SIGNED or STANDARD
    rate_request.RequestedShipment.FreightShipmentDetail.CollectTermsType = 'STANDARD'

    package1_weight = rate_request.create_wsdl_object_of_type('Weight')
    package1_weight.Value = 500.0
    package1_weight.Units = "LB"

    rate_request.RequestedShipment.FreightShipmentDetail.PalletWeight = package1_weight

    package1 = rate_request.create_wsdl_object_of_type('FreightShipmentLineItem')
    package1.Weight = package1_weight
    package1.Packaging = 'PALLET'
    package1.Description = 'Products'
    package1.FreightClass = 'CLASS_500'

    rate_request.RequestedShipment.FreightShipmentDetail.LineItems = package1

    # If you'd like to see some documentation on the ship service WSDL, un-comment
    # this line. (Spammy).
    # print rate_request.client

    # Un-comment this to see your complete, ready-to-send request as it stands
    # before it is actually sent. This is useful for seeing what values you can
    # change.
    # print rate_request.RequestedShipment

    # Fires off the request, sets the 'response' attribute on the object.
    rate_request.send_request()

    # This will show the reply to your rate_request being sent. You can access the
    # attributes through the response attribute on the request object. This is
    # good to un-comment to see the variables returned by the FedEx reply.
    # print rate_request.response

    # Here is the overall end result of the query.
    print "HighestSeverity:", rate_request.response.HighestSeverity

    # RateReplyDetails can contain rates for multiple ServiceTypes if ServiceType was set to None
    for service in rate_request.response.RateReplyDetails:
        for detail in service.RatedShipmentDetails:
            for surcharge in detail.ShipmentRateDetail.Surcharges:
                if surcharge.SurchargeType == 'OUT_OF_DELIVERY_AREA':
                    print "%s: ODA rate_request charge %s" % (service.ServiceType, surcharge.Amount.Amount)

        for rate_detail in service.RatedShipmentDetails:
            print "%s: Net FedEx Charge %s %s" % (service.ServiceType, rate_detail.ShipmentRateDetail.TotalNetFedExCharge.Currency, rate_detail.ShipmentRateDetail.TotalNetFedExCharge.Amount)


def postal_inquiry(doc_fedex_shipment):
    config_obj = fedex_config.get(doc_fedex_shipment.fedex_settings)

    inquiry = PostalCodeInquiryRequest(config_obj)
    inquiry.PostalCode = '29631'
    inquiry.CountryCode = 'US'

    # Fires off the request, sets the 'response' attribute on the object.
    inquiry.send_request()

    # See the response printed out.
    print inquiry.response


def validate_address(doc_fedex_shipment):
    config_obj = fedex_config.get(doc_fedex_shipment.fedex_settings)

    # This is the object that will be handling our tracking request.
    connection = FedexAddressValidationRequest(config_obj)

    # The AddressValidationOptions are created with default values of None, which
    # will cause WSDL validation errors. To make things work, each option needs to
    # be explicitly set or deleted.

    # Set the flags we want to True (or a value).
    connection.AddressValidationOptions.CheckResidentialStatus = True
    connection.AddressValidationOptions.VerifyAddresses = True
    connection.AddressValidationOptions.RecognizeAlternateCityNames = True
    connection.AddressValidationOptions.MaximumNumberOfMatches = 3

    # Delete the flags we don't want.
    del connection.AddressValidationOptions.ConvertToUpperCase
    del connection.AddressValidationOptions.ReturnParsedElements

    # *Accuracy fields can be TIGHT, EXACT, MEDIUM, or LOOSE. Or deleted.
    connection.AddressValidationOptions.StreetAccuracy = 'LOOSE'
    del connection.AddressValidationOptions.DirectionalAccuracy
    del connection.AddressValidationOptions.CompanyNameAccuracy

    # Create some addresses to validate
    address1 = connection.create_wsdl_object_of_type('AddressToValidate')
    address1.CompanyName = 'International Paper'
    address1.Address.StreetLines = ['155 Old Greenville Hwy', 'Suite 103']
    address1.Address.City = 'Clemson'
    address1.Address.StateOrProvinceCode = 'SC'
    address1.Address.PostalCode = 29631
    address1.Address.CountryCode = 'US'
    address1.Address.Residential = False
    connection.add_address(address1)

    address2 = connection.create_wsdl_object_of_type('AddressToValidate')
    address2.Address.StreetLines = ['320 S Cedros', '#200']
    address2.Address.City = 'Solana Beach'
    address2.Address.StateOrProvinceCode = 'CA'
    address2.Address.PostalCode = 92075
    address2.Address.CountryCode = 'US'
    connection.add_address(address2)

    # Send the request and print the response
    connection.send_request()
    print connection.response


@frappe.whitelist()
def make_fedex_shipment(source_name, target_doc=None):
    def postprocess(source, target):
        doc_delivery_note = frappe.get_doc('Delivery Note', source.delivery_note)

        # updating preferred currency that is used for returned from Fedex totals
        target.preferred_currency = doc_delivery_note.currency

        # updating shipper address
        warehouse = frappe.db.get('Warehouse', source.items[0].warehouse)
        if not warehouse:
            warehouse = frappe.db.get('Warehouse', doc_delivery_note.items[0].warehouse)
            if not warehouse:
                frappe.throw('Neither Packing Slip Item nor Delivery Note Item has warehouse set.')
        target.shipper_address_address_line_1 = warehouse.address_line_1
        target.shipper_address_city = warehouse.city
        target.shipper_address_state_or_province_code = countries.get_country_state_code(warehouse.country, warehouse.state)
        target.shipper_address_postal_code = warehouse.postal_code or warehouse.pin
        target.shipper_address_residential = 0
        if not warehouse.country:
            frappe.throw('Please specify country in Warehouse %s' % warehouse.name)
        target.shipper_address_country_code = countries.get_country_code(warehouse.country)
        target.shipper_contact_person_name = warehouse.company
        target.shipper_contact_company_name = warehouse.company
        target.shipper_contact_phone_number = warehouse.phone_no

        # updating recipient address
        target.customer = doc_delivery_note.customer
        doc_customer = frappe.get_doc("Customer", doc_delivery_note.customer)
        if doc_delivery_note.shipping_address_name:
            target.fedex_settings = utils.get_fedex_settings(doc_delivery_note.company)
            shipping_address = frappe.db.get('Address', doc_delivery_note.shipping_address_name)
            target.recipient_address = shipping_address.name
            target.recipient_address_address_line_1 = shipping_address.address_line1
            target.recipient_address_city = shipping_address.city
            target.recipient_address_state_or_province_code = countries.get_country_state_code(shipping_address.country, shipping_address.state)
            target.recipient_address_postal_code = shipping_address.pincode
            target.recipient_address_country_code = countries.get_country_code(shipping_address.country)
            target.recipient_contact_person_name = shipping_address.customer_name
            if doc_customer.customer_type == "Company":
                target.recipient_contact_company_name = doc_customer.customer_name
                if not target.recipient_contact_company_name:
                    target.recipient_contact_company_name = shipping_address.customer_name
            else:
                target.recipient_contact_company_name = shipping_address.customer_name
            target.recipient_contact_phone_number = shipping_address.phone

            customer_type = frappe.db.get_value('Customer', shipping_address.customer, 'customer_type')
            target.recipient_address_residential = 0 if customer_type == 'Company' else 1
        else:
            frappe.msgprint('Shipping Address is missed in Delivery Note %s' % doc_delivery_note.name)

        # create shipment packages
        merged_packages = {}
        for item in source.items:
            packages = json.loads(item.packages_json or "{}")
            for pckg_no in packages:
                merged_pckg = merged_packages.get("pckg_no")
                if merged_pckg:
                    pckg = packages.get("pckg_no")
                    for item_code in pckg:
                        if merged_pckg.get("item_code"):
                            merged_pckg[item_code] += pckg[item_code]
                        else:
                            merged_pckg[item_code] = pckg[item_code]
                else:
                    merged_packages[pckg_no] = packages[pckg_no]
        target.set("packages", [])
        for idx, key in enumerate(merged_packages.keys(), 1):
            fedex_package = frappe.new_doc("Fedex Package")
            target.append("packages", {
                "doctype": fedex_package.doctype,
                "dimensions_units": fedex_package.dimensions_units,
                "width": fedex_package.width,
                "height": fedex_package.height,
                "length": fedex_package.length,
                "weight_units": fedex_package.weight_units,
                "weight_value": fedex_package.weight_value,
                "idx": idx
            })

    if frappe.db.get_value('Packing Slip', source_name, 'fedex_shipment') or frappe.db.get_value('Packing Slip', source_name, 'oc_tracking_number'):
        frappe.throw('Cannot make new Fedex Shipment: either Fedex Shipment is already created or tracking number is set.')

    doclist = get_mapped_doc('Packing Slip', source_name, {
        'Packing Slip': {
            'doctype': 'Fedex Shipment',
            'field_map': {
                'name': 'packing_slip'
            },
            'validation': {
                'docstatus': ['=', 0]
            }
        }
    }, target_doc, postprocess)

    return doclist


@frappe.whitelist()
def get_address_details(address):
    address = frappe.db.get('Address', address)
    if address:
        address['state_or_province_code'] = countries.get_country_state_code(address.country, address.state)
        address['country_code'] = countries.get_country_code(address.country)

        customer_type = frappe.db.get_value('Customer', address.customer, 'customer_type')
        address['residential'] = 0 if customer_type == 'Company' else 1
        return address


@frappe.whitelist()
def get_all_fedex_labels_file_url(fedex_shipment):
    for fs in frappe.get_all('File Data', fields=['file_name', 'file_url'],
        filters = {'attached_to_name': fedex_shipment, 'attached_to_doctype': 'Fedex Shipment'}):
        if fs.get('file_name', '').startswith('all_fedex_labels_') and fs.get('file_name', '').endswith('.pdf'):
            return fs.get('file_url')
