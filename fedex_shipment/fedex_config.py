from __future__ import unicode_literals

import frappe

from fedex.config import FedexConfig


def get(fedex_settings):
    fedex_settings = frappe.get_doc("Fedex Settings", fedex_settings)
    return FedexConfig(key=fedex_settings.key,
                       password=fedex_settings.password,
                       account_number=fedex_settings.account_number,
                       meter_number=fedex_settings.meter_number,
                       freight_account_number=fedex_settings.freight_account_number,
                       use_test_server=fedex_settings.use_test_server)
