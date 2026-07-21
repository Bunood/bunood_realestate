# Bunood Real Estate

Real estate & leasing management for **ERPNext v16**, natively integrated with
ERPNext accounting. A clean, standalone Frappe app — **zero core modification**.

## Design principle

The app owns the **domain layer** (what & when): `Property`, `Real Estate Unit`,
`Lease Contract`, `Rent Schedule`. **ERPNext owns all money** (Sales Invoice,
Payment Entry, GL). We never write GL directly and never duplicate a master
(a tenant *is* a `Customer`). Linkage is via **Accounting Dimensions**
(`Property` + `Unit`), so per-property/unit P&L and ledgers come from ERPNext's
own reports. Full architecture: `../bunood_erpnext/docs/plan-realestate.md`.

## Status

**Phase 0 — foundation**
- [x] Installable Frappe app skeleton + module **Real Estate**
- [x] `Real Estate Settings` (Single) — accounting defaults, config-over-code
- [ ] Saudi market setup (SAR, Arabic/RTL, VAT 15%) — site-setup + fixtures

**Phase 1 — property & units**
- [x] `Property` doctype (autoname `PROP-.#####`, address reuses ERPNext Address)
- [x] `Real Estate Unit` doctype (unique per `{property}-{unit_no}`, status lifecycle)
- [x] Property form: **Create Units** bulk dialog + Units connections list
- [x] `Property` + `Real Estate Unit` registered as **Accounting Dimensions** (fixture)

**Phase 2 — lease contract**
- [x] `Lease Contract` (submittable) + `Lease Unit` child (multi-unit, per-unit annual rent)
- [x] `contract_type` residential/commercial (VAT driver) + subtype new/renewal/transfer
- [x] Guards: commercial ZATCA VAT number `^3\d{13}3$`, unit non-overlap, date order
- [x] On submit → units Occupied + `current_lease` set; on cancel → reverted
- [x] **No mid-contract escalation** (flat rent; increases only on renewal — matches bunood_core)

**Phase 3 — rent schedule**
- [x] `Rent Schedule` (standalone, indexed on lease/due_date/status) — one Planned row per period
- [x] Deterministic generator on lease submit: due_date = period start, installments over the actual term, final-period proration (unit-tested: year=12, quarter=4, 3-month=3, partial June-15 = 5000)
- [x] `base_amount` is ex-VAT; VAT applied on the Sales Invoice by contract type (Phase 4)
- [x] Lease cancel deletes Planned rows / marks invoiced ones Cancelled

**Phase 4 — invoice generation**
- [x] Daily scheduler `generate_due_rent_invoices` (hooks) + manual "Generate Due Invoices" button
- [x] Due Planned rows → **submitted accrual `Sales Invoice`** («معلّق»/Unpaid), posting_date = due date
- [x] **One line per unit**, tagged with Property + Real Estate Unit accounting dimensions
- [x] VAT by contract type: commercial 15% / residential exempt template (from settings)
- [x] Idempotent (per-row txn, re-check guard), fail-loud-per-row via `frappe.log_error`
- [x] Amount split across units sums exactly (unit-tested)

**Phase 5 — collections & deposits**
- [x] `doc_events` on Sales Invoice + Payment Entry mirror invoice status (Unpaid/Overdue/Paid) onto the Rent Schedule row — no parallel ledger
- [x] Invoice cancel resets the row to Planned (re-invoiceable; avoids stuck-period leak)
- [x] Security deposit as a liability via native Journal Entry (receive + refund), tracked on the lease
- [x] Collections themselves are standard ERPNext Payment Entry against the «معلّق» invoice

**Phase 6 — lifecycle & reports**
- [x] Renewal: `renew_lease` clones the lease (dates shifted, rent bumped by %) → Draft; submitting it marks the parent Renewed
- [x] `Lease Termination` (submittable) + `Lease Deduction` child: cancels future planned rent, settles the deposit (DR liability / CR net refund + CR deduction income) via one Journal Entry, frees the units
- [x] Reports: **Rent Roll** (active leases × units) and **Occupancy Summary** (per-property vacant/occupied + occupancy %)
- [x] Renew / Terminate / deposit buttons on the Lease Contract form

**Real-estate module: feature-complete for the core leasing cycle.** Remaining before
production: install on a live v16 bench (WSL2), configure Real Estate Settings + the two
Saudi VAT templates, and run the end-to-end flow (property → units → lease → schedule →
invoice → payment → renew/terminate).

## Reviewed

An adversarial multi-dimension review (ERPNext API, doctype schema, domain/financial
logic, install/migration, security) ran before deployment; all confirmed findings were
fixed: schedule month-end/Feb-29 clamping + cumulative rounding, invoice-generation row
lock + lease-status gate + terminal Failed state on persistent errors, termination-cancel
rent restore + P&L cost centers, draft-invoice on_trash revert, report company scoping,
lease-cancel guard against orphaning issued invoices, unit double-book lock, and
bulk-create-units cap. Pure schedule/split logic is unit-tested (incl. leap-year and
non-divisible totals).

## Known limitations

- **One company per site.** Real Estate Settings is a single doctype holding
  company-specific accounts; the invoice generator refuses a lease whose company differs
  from the settings company. Multi-company on one site needs per-company account mappings
  (future). Fine for the site-per-tenant SaaS model.
- **Final ERPNext integration is unverified on a live bench** — the Sales Invoice / Payment
  Entry / Journal Entry paths need one end-to-end run on v16 before real tenants.

## Domain reference

Types and rules are extracted from **bunood_core** (`D:/my projects/core`, Django) — the prior
system. Notably the Saudi VAT rule: **residential rent is VAT-exempt, commercial is 15%**
(`property_type` wins; Mixed decides by unit type; any commercial unit ⇒ commercial lease).
bunood_core's financial layer (parallel RentInvoice + treasury vouchers + recomputed ledgers)
is deliberately NOT carried over — ERPNext owns all money.

## Install

```bash
# from your bench directory (WSL2 / Linux):
bench get-app https://github.com/<org>/bunood_realestate
bench --site <site> install-app bunood_realestate
bench --site <site> migrate
```

For the Bunood image, add this repo to `../bunood_erpnext/apps.json` (pinned to a
tag/SHA) so it is baked in, then install per tenant.

## Saudi setup (done at/after site creation)

Currency **SAR** and language **Arabic (RTL)** are set when the site is created
(`bench new-site … --set-default … `) / in the setup wizard. A **VAT 15%** Sales
Taxes and Charges Template and the rent income / receivable / tenant-deposit
accounts are selected once in **Real Estate Settings**. ZATCA e-invoicing is added
later via a separate app (`ksa_compliance`) that hooks the standard Sales Invoice.

## Development

Ruff + pre-commit for linting; tests run as
`bench --site <test_site> run-tests --app bunood_realestate`.
