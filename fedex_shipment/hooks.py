# -*- coding: utf-8 -*-
from __future__ import unicode_literals

app_name = "fedex_shipment"
app_title = "Fedex Shipment"
app_publisher = "olhonko"
app_description = "The application to provide shipments with Fedex"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "olhonko@gmail.com"
app_version = "0.0.1"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/fedex_shipment/css/fedex_shipment.css"
# app_include_js = "/assets/fedex_shipment/js/fedex_shipment.js"

# include js, css files in header of web template
# web_include_css = "/assets/fedex_shipment/css/fedex_shipment.css"
# web_include_js = "/assets/fedex_shipment/js/fedex_shipment.js"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
#   "Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Installation
# ------------

# before_install = "fedex_shipment.install.before_install"
# after_install = "fedex_shipment.install.after_install"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "fedex_shipment.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
#   "Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
#   "Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    "Fedex Shipment": {
        "validate": "fedex_shipment.shipment.validate",
        "on_submit": "fedex_shipment.shipment.on_submit",
        "before_submit": "fedex_shipment.shipment.before_submit",
        "before_cancel": "fedex_shipment.shipment.before_cancel"
    }
}


doctype_js = {
    "Packing Slip": ["custom_scripts/packing_slip.js"]
}


# Scheduled Tasks
# ---------------

# scheduler_events = {
#   "all": [
#       "fedex_shipment.tasks.all"
#   ],
#   "daily": [
#       "fedex_shipment.tasks.daily"
#   ],
#   "hourly": [
#       "fedex_shipment.tasks.hourly"
#   ],
#   "weekly": [
#       "fedex_shipment.tasks.weekly"
#   ]
#   "monthly": [
#       "fedex_shipment.tasks.monthly"
#   ]
# }

# Testing
# -------

# before_tests = "fedex_shipment.install.before_tests"

# Overriding Whitelisted Methods
# ------------------------------
#
# override_whitelisted_methods = {
#   "frappe.desk.doctype.event.event.get_events": "fedex_shipment.event.get_events"
# }
