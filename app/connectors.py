"""Accounting / ERP platforms shown on the connect page. Only entries with ready=True work today."""

CONNECTORS = [
    {"id": "zoho", "name": "Zoho Books", "color": "#d9382b", "ready": True,
     "blurb": "Pull vendor bills and the rates you paid."},
    {"id": "tally", "name": "Tally Prime", "color": "#1b6ec2", "ready": False,
     "blurb": "Purchase vouchers from Tally (common in India)."},
    {"id": "sap", "name": "SAP", "color": "#0a6ed1", "ready": False,
     "blurb": "S/4HANA and Business One purchase documents."},
    {"id": "netsuite", "name": "Oracle NetSuite", "color": "#c74634", "ready": False,
     "blurb": "Vendor bills and purchase orders."},
    {"id": "dynamics", "name": "Microsoft Dynamics 365", "color": "#4b53bc", "ready": False,
     "blurb": "Purchase invoices from Business Central / Finance."},
    {"id": "quickbooks", "name": "QuickBooks", "color": "#2ca01c", "ready": False,
     "blurb": "Bills and vendor transactions."},
    {"id": "xero", "name": "Xero", "color": "#13b5ea", "ready": False,
     "blurb": "Supplier bills and line items."},
    {"id": "busy", "name": "Busy", "color": "#e08a00", "ready": False,
     "blurb": "Purchase vouchers from Busy accounting."},
    {"id": "odoo", "name": "Odoo", "color": "#714b67", "ready": False,
     "blurb": "Vendor bills from Odoo Accounting."},
    {"id": "sage", "name": "Sage", "color": "#00a86b", "ready": False,
     "blurb": "Purchase invoices from Sage Intacct / 50."},
]
