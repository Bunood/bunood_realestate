app_name = "bunood_realestate"
app_title = "Bunood Real Estate"
app_publisher = "Bunood"
app_description = "Real estate & leasing management, natively integrated with ERPNext accounting"
app_email = "info@bunood.example"
app_license = "mit"

# Apps this app builds on. ERPNext owns all accounting (Sales Invoice, Payment Entry, GL);
# this app only owns the domain layer (Property, Unit, Lease, Rent Schedule).
required_apps = ["erpnext"]

# Design tokens — the SINGLE source of the "Sadu Modern" palette, loaded on BOTH surfaces
# (desk + website). Every page/wizard/portal references --bnd-* (or a legacy alias) from here;
# no brand hex is hardcoded anywhere else.
app_include_css = "/assets/bunood_realestate/css/bunood_tokens.css"

# Portal (website) styling — tokens first, then the portal skin scoped to .bnd-portal so
# it only affects the tenant / owner / contractor portals, never the rest of the site.
web_include_css = [
    "/assets/bunood_realestate/css/bunood_tokens.css",
    "/assets/bunood_realestate/css/bunood_portal.css",
]

# ZATCA bridge (bunood_zatca `zatca_original_invoice` hook): our termination credit
# notes are standalone (no return_against) — resolve their original invoice through
# the Lease Termination Credit link so the e-invoice BillingReference is correct.
zatca_original_invoice = [
    "bunood_realestate.real_estate.events.zatca_original_for_credit_note",
]

# Wrapper doctrine (docs/plan-financial-reporting.md Phase 3): preset report entries
# on the core party forms via the supported doctype_js hook — never a core file edit.
doctype_js = {
    "Customer": "public/js/bnd_customer.js",
    "Supplier": "public/js/bnd_supplier.js",
}

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
    {"dt": "Accounting Dimension", "filters": [["document_type", "in", ["Property", "Real Estate Unit", "Land"]]]},
    # Bunood Core: seed default Charge Types (Broker/Cleaning/… ) for the Charge engine.
    "Charge Type",
    # Master data (user-editable taxonomy) — seeded with common Saudi values.
    # Behavior-driving ones (Management Model / Revenue Model / Contract Kind) carry a
    # `behavior` engine-key that the code handles; new rows pick a known key.
    "RE Property Type",
    "RE Ownership Type",
    "RE Business Type",
    "RE Management Model",
    "RE Revenue Model",
    "RE Contract Kind",
    "RE Maintenance Category",
    # Unit fixtures & furniture taxonomy (الأثاث والتجهيزات) — user-extensible.
    "Inventory Item Type",
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
        # Master-lease: turn due head-lease rows into Purchase Invoices to the owner.
        "bunood_realestate.real_estate.head_lease.generate_due_head_lease_bills",
        # Collections: charge a late fee on overdue, still-unpaid rent invoices
        # (no-op unless enabled in Real Estate Settings).
        "bunood_realestate.real_estate.collections.apply_late_fees",
        # Expire abandoned unit holds so a unit never stays stuck "Reserved".
        "bunood_realestate.real_estate.doctype.unit_booking.unit_booking.expire_bookings",
        # Expire Active leases past their end_date and free their units, so a unit never
        # stays "Occupied" after its lease ended (keeps occupancy KPIs + wizard truthful).
        "bunood_realestate.real_estate.doctype.lease_contract.lease_contract.expire_due_leases",
        # Notifications (no-op unless enabled in Real Estate Settings): alert on leases
        # expiring soon (T-60/30/7) and on tenants with overdue rent. Idempotent (logged once).
        "bunood_realestate.real_estate.notifications.notify_expiring_leases",
        "bunood_realestate.real_estate.notifications.send_overdue_reminders",
        # Prepare Draft renewals for auto-renew leases nearing expiry (reuses renew_lease).
        "bunood_realestate.real_estate.notifications.auto_draft_renewals",
        # Charge Engine: turn due Planned Charge Schedule rows (utilities/services) into
        # native Sales Invoices, grouped by each lease's Billing Policy. Rent-independent.
        "bunood_realestate.real_estate.charge_engine.generate_due_charge_invoices",
    ],
}

# ------------------------------------------------------------------------------
# Document events — react to ERPNext docs without modifying core.
# Phase 5: reflect Sales Invoice / Payment Entry status back onto Rent Schedule rows.
# ------------------------------------------------------------------------------
doc_events = {
    "Sales Invoice": {
        # Phase 0 (plan-financial-reporting.md): dimension enforcement — RE rows must
        # carry Property before they reach the GL. In-app validation, never a core edit.
        "validate": "bunood_realestate.real_estate.dimension_guard.validate_dimensions",
        "on_submit": "bunood_realestate.real_estate.events.sync_rent_schedule_on_invoice",
        "on_update_after_submit": "bunood_realestate.real_estate.events.sync_rent_schedule_on_invoice",
        "on_cancel": [
            "bunood_realestate.real_estate.events.sync_rent_schedule_on_invoice",
            "bunood_realestate.real_estate.charge_engine.reset_charge_schedule_on_invoice",
        ],
        "on_trash": [
            "bunood_realestate.real_estate.events.sync_rent_schedule_on_invoice",
            "bunood_realestate.real_estate.charge_engine.reset_charge_schedule_on_invoice",
        ],
    },
    "Payment Entry": {
        # Phase 0: RE payments must carry the settled invoices' Property AND a Mode of
        # Payment (طريقة الدفع) — statements/Owner Ledger show how every riyal moved.
        "validate": "bunood_realestate.real_estate.dimension_guard.validate_dimensions",
        "on_submit": "bunood_realestate.real_estate.events.sync_rent_schedule_on_payment",
        "on_cancel": [
            "bunood_realestate.real_estate.events.sync_rent_schedule_on_payment",
            # Cash-basis: a cancelled settling payment may leave an owner over-paid — flag it.
            "bunood_realestate.real_estate.events.flag_owner_payout_on_payment_cancel",
        ],
    },
    # Keep the lease's cached deposit balance in step with the GL: if a deposit /
    # refund Journal Entry is cancelled or deleted, reset the mirror so the app never
    # refunds/settles against a liability that no longer exists (no parallel ledger).
    "Journal Entry": {
        "validate": "bunood_realestate.real_estate.dimension_guard.validate_dimensions",
        # An amended payout JE (cancel-then-resubmit) must re-attach its Owner Payout, or the
        # re-created owner credit would sit in the GL with no Posted payout guarding re-runs.
        "on_submit": "bunood_realestate.real_estate.events.relink_owner_payout_on_je_amend",
        "on_cancel": [
            "bunood_realestate.real_estate.events.reconcile_deposit_on_je",
            "bunood_realestate.real_estate.events.reconcile_owner_payout_on_je",
        ],
        "on_trash": [
            "bunood_realestate.real_estate.events.reconcile_deposit_on_je",
            "bunood_realestate.real_estate.events.owner_payout_unlink_on_je_trash",
        ],
    },
    # A renamed Company must carry its Real Estate Company Profile with it (the profile is
    # autonamed by company; link propagation updates the field but not the doc name).
    "Company": {
        "after_rename": "bunood_realestate.real_estate.events.rename_company_profile",
    },
    # If a Maintenance Work Order's contractor bill is cancelled/deleted, clear the work
    # order's link so a corrected bill can be re-posted (reset-on-cancel discipline).
    "Purchase Invoice": {
        "validate": "bunood_realestate.real_estate.dimension_guard.validate_dimensions",
        "on_cancel": "bunood_realestate.real_estate.events.reset_work_order_on_pi_cancel",
        "on_trash": "bunood_realestate.real_estate.events.reset_work_order_on_pi_cancel",
    },
    # Phase 0 dimension guard also covers employee-claimed property expenses.
    "Expense Claim": {
        "validate": "bunood_realestate.real_estate.dimension_guard.validate_dimensions",
    },
}
