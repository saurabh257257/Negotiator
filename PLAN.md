# Negotiator: Product Plan

B2B SaaS for the **battery and EV industry**. A manufacturer (the buyer) uses it to renegotiate vendor prices.
When a vendor raises prices citing a cost driver (e.g. "lithium carbonate +45%"), Negotiator tracks that driver
and alerts the buyer when the justification weakens, with an evidence-backed brief to take back to the vendor.

Sold to other companies: multi-tenant from day one (organizations, users, tenant-scoped data).
First reference customer: a battery pack manufacturer buying cells, BMS and epoxy from China and
cell holders, battery boxes, busbars etc. from Indian vendors.

## Core logic

    justified_price = old_price * (1 + sum(weight_i * direction_i * change_i))     (in the vendor's quote currency)

- `weight_i`: share of the component's vendor price attributable to driver i (template default, buyer can override)
- `change_i`: % change since the baseline (when the old price was set), optionally lagged
- `gap = vendor_price / justified_price - 1`; alert when above a threshold and a minimum rupee amount
- Baseline is the driver level at the time the old price was set. A 50% rise followed by a 25% fall
  is still +12.5% vs baseline (a 33% fall is needed to return to baseline).
- Claim check: compare the vendor's stated increase with what the drivers supported at the time.

### Landed-cost layer (imported components)

Imports are quoted in USD or CNY, so the buyer's INR cost has three separate causes:

    landed_inr = vendor_price * fx * (1 + freight_share * (1 + freight_change) + duty)

| Effect | Negotiable with vendor? |
|---|---|
| Vendor price vs justified price (commodity-driven) | Yes. This is the main lever. |
| FX move (USD/INR, CNY/INR) | No (hedging or invoicing-currency decision) |
| Freight move | With the forwarder / Incoterm, not the vendor |

The UI shows a **landed-cost bridge** (old landed cost, then vendor, FX and freight effects, then new landed cost)
so the buyer doesn't confuse an FX loss with vendor overcharging, and vice versa.
Domestic components are quoted in INR and have no landed layer.

## Cost drivers

| Driver | Type | Source (v1) |
|---|---|---|
| Copper, aluminum, crude oil (energy / plastics proxy) | Commodity / transport | Yahoo Finance (HG=F, ALI=F, BZ=F) |
| USD/INR, CNY/INR | FX | Yahoo Finance (USDINR=X, CNYINR=X) |
| Lithium carbonate, cobalt, nickel | Commodity | Manual / CSV benchmark upload |
| Steel (HRC), polymer resin, epoxy raw (BPA/ECH), electronics index | Commodity proxy | Manual / CSV benchmark upload |
| Container freight | Transport | Manual / CSV benchmark upload |

Free sources don't cover battery materials, so v1 combines free proxies with benchmark uploads.
A paid provider (Fastmarkets, SMM, Benchmark Minerals) can be added later behind the same Driver interface.
Free futures are a market indicator, not contract-grade proof. Brief wording must reflect that.

## Category templates (starter library)

Each component inherits a template: default driver weights, imported/domestic flag, landed-cost shares.

| Template | Origin | Main drivers |
|---|---|---|
| Li-ion cell | Imported | lithium carbonate, cobalt, nickel, copper, aluminum |
| BMS | Imported | electronics index, copper |
| Epoxy / potting | Imported | epoxy raw (BPA/ECH), crude |
| Electrolyte & separator | Imported | lithium carbonate, resin |
| Cell holder (plastic) | Domestic | resin, crude |
| Battery box (sheet metal) | Domestic | steel, aluminum |
| Busbar & nickel strip | Domestic | copper, nickel |
| Wires & connectors | Domestic | copper, resin |
| Packaging & foam | Domestic | resin, crude |

AI assigns a template to each uploaded component with a confidence score. Low confidence goes to a review queue.

## Working at scale

- Starts at ~1k components per company, designed so it can grow (Postgres-compatible schema, batch compute).
- The negotiation unit is the **vendor price event** ("Vendor X raised 18 components on Aug 1, citing lithium"),
  with one combined brief covering all affected components ranked by money at stake.
- Dashboard hierarchy: vendor, then category, then component.
- Prioritize by annual rupees at stake (gap x volume x landed factor), with configurable thresholds.
- Alerts are grouped into a weekly digest, not one per component.
- Bulk import by CSV/Excel with column mapping, template assignment and an error report.

## Data model

- Organization, User
- Vendor (origin: imported/domestic, quote currency), Component (part no, unit, volume)
- Template, TemplateWeight, ComponentTemplate (confidence, reviewed flag, override)
- Driver (type, symbol, source: yahoo | manual), DriverPrice (daily history, shared across tenants for public sources, tenant-scoped for manual)
- PriceEvent (vendor, date, stated reason, baseline driver levels) and PriceChange (component, old, new)
- Alert, Negotiation (status: Draft | Sent | Countered | Accepted | Rejected; final price, savings)

## Screens

Dashboard (executive + procurement views), Vendors (list and detail), Components (data table with filters,
bulk actions, CSV export, detail with landed-cost bridge), Review queue, Templates, Drivers (with benchmark upload),
Import, Negotiations board, Negotiation brief (vendor-level, editable, PDF/email).

## Decisions made

- Input: CSV/Excel import and manual entry (ERP connectors later)
- Market data: free Yahoo Finance for what it covers, manual/CSV benchmarks for battery materials
- Cost shares: starter template library shipped by us, buyer can override, Claude assigns templates
- Imported items tracked in both vendor currency and landed INR
- Negotiation unit: vendor event covering many components
- Output: dashboard + alerts + negotiation brief. No auto-send to vendors.
- Market: battery and EV industry only (domain-specific features such as chemistry-aware templates are allowed)
- Product sold to other companies

## Build phases

1. Engine: drivers, templates, component assignment, price events, justified-price and landed-cost compute, nightly batch
2. Import: CSV upload, AI template assignment, review queue
3. Core UI: vendor / category / component hierarchy, data table, prioritized dashboard
4. Negotiation: vendor-level briefs, alerts and digests, outcome tracking
5. Production hardening: auth, tenant isolation, Postgres, billing

## Risks

- Battery-material price data is not free; benchmark credibility depends on the source
- BMS electronics has no clean public benchmark (needs a proxy or buyer-supplied index)
- Many contracts are index-linked or fixed-price with adjustment clauses, which limit leverage
- Vendor input lag: relief may not reach their cost for 30-90 days (show expected relief date)
- Strategic vendors: briefs need a collaborative tone, and relationship risk is high
- Customs duty and incoterms vary per shipment, so landed-cost shares are estimates

## Possible later add-on

BOM roll-up: how component cost changes flow into the cost of a finished battery pack, for repricing the buyer's own products.
