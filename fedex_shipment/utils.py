from __future__ import unicode_literals

import frappe
from frappe.utils import flt


def get_fedex_settings(company):
    all_fedex_settings = get_all_fedex_settings(company)
    if all_fedex_settings:
        return all_fedex_settings[0][0]


def get_all_fedex_settings(company):
    fs = frappe.db.sql("""select fs.name from `tabFedex Settings` fs
        where (exists(select company from `tabFedex Settings Company` fsc where fsc.parent = fs.name and fsc.company=%(company)s ))""", {"company": company})
    if fs:
        return fs
    else:
        return []


def get_amount(required_currency, actual_currency, amount, from_currency, into_currency, rate):
    if required_currency.upper() == actual_currency.upper():
        return flt(amount)
    elif required_currency.upper() == into_currency.upper() and actual_currency.upper() == from_currency.upper():
        return flt(amount) * flt(rate)
    elif required_currency.upper() == from_currency.upper() and actual_currency.upper() == into_currency.upper():
        return flt(amount) / flt(rate)
    frappe.throw('Cannot get amount in required currency %s from %s using conversion from_currency=%s and into_currency=%s' % (required_currency, actual_currency, from_currency, into_currency))
