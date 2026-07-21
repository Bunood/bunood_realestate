app_name = "bunood_realestate"
app_title = "Bunood Real Estate"
app_publisher = "Bunood"
app_description = "Real estate & leasing management, natively integrated with ERPNext accounting"
app_email = "info@bunood.example"
app_license = "mit"

# Apps this app builds on. ERPNext owns all accounting (Sales Invoice, Payment Entry, GL);
# this app only owns the domain layer (Property, Unit, Lease, Rent Schedule).
required_apps = ["erpnext"]

# ------------------------------------------------------------------------------
# Fixtures — DATA (not code) shipped with the app; installs on every site.
# This is how we extend ERPNext WITHOUT touching core (see docs/plan-realestate.md §0.2):
# Phase 1 exports Custom Fields (Property/Unit on Sales Invoice) + the Property and
# Real Estate Unit Accounting Dimensions here. Uncomment as they are created.
# ------------------------------------------------------------------------------
fixtures = [
    # Property + Real Estate Unit as Accounting Dimensions → every Sales Invoice and
    # GL Entry is tagged by property & unit, giving native per-property/unit P&L and
    # ledgers with zero parallel bookkeeping. Installed on migrate; the dimension's
    # after_insert builds the custom fields across ERPNext transaction doctypes (one-time).
    {"dt": "Accounting Dimension", "filters": [["document_type", "in", ["Property", "Real Estate Unit"]]]},
    # Phase 2+: Custom Fields (e.g. Lease/Property/Unit links on Sales Invoice) export here too.
    # {"dt": "Custom Field", "filters": [["module", "=", "Real Estate"]]},
]

# ------------------------------------------------------------------------------
# Scheduled jobs — Phase 4: daily accrual rent-invoice generation from Rent Schedule.
# Runs in the background worker, never in a web request (performance: see plan §0.1).
# ------------------------------------------------------------------------------
scheduler_events = {
    "daily": [
        # Turn due Planned Rent Schedule rows into submitted accrual Sales Invoices.
        "bunood_realestate.real_estate.tasks.generate_due_rent_invoices",
    ],
}

# ------------------------------------------------------------------------------
# Document events — react to ERPNext docs without modifying core.
# Phase 5: reflect Sales Invoice / Payment Entry status back onto Rent Schedule rows.
# ------------------------------------------------------------------------------
doc_events = {
    "Sales Invoice": {
        "on_submit": "bunood_realestate.real_estate.events.sync_rent_schedule_on_invoice",
        "on_update_after_submit": "bunood_realestate.real_estate.events.sync_rent_schedule_on_invoice",
        "on_cancel": "bunood_realestate.real_estate.events.sync_rent_schedule_on_invoice",
        "on_trash": "bunood_realestate.real_estate.events.sync_rent_schedule_on_invoice",
    },
    "Payment Entry": {
        "on_submit": "bunood_realestate.real_estate.events.sync_rent_schedule_on_payment",
        "on_cancel": "bunood_realestate.real_estate.events.sync_rent_schedule_on_payment",
    },
}
