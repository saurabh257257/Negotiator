# Negotiator

Helps battery/EV manufacturers renegotiate vendor prices. See [PLAN.md](PLAN.md) for the product plan and
`mockup/index.html` for the UI mockup.

## What works today

1. **Login** with a mobile number (demo number `123`; real OTP login is not built yet).
2. **Connect page** showing connectors for the major accounting/ERP platforms (Zoho Books, Tally Prime, SAP,
   Oracle NetSuite, Microsoft Dynamics 365, QuickBooks, Xero, Busy, Odoo, Sage). **Only Zoho Books works
   today**; the rest show "Coming soon" (list in `app/connectors.py`). The Zoho connector lists the
   read-only permissions first, then sends the user to Zoho's own sign-in and consent page (OAuth 2.0).
   Negotiator never sees the Zoho password.
3. **Bills sync**: pulls the last ~6 months of Zoho Books **bills** and their line items.
4. **Purchase rates page**: a vendor tree on the left, with each vendor expanding into its products.
   Selecting a vendor shows all its products with purchase rates (latest, average weighted by quantity,
   min to max, change, trend). Selecting a product shows its rate history chart, every purchase, and how
   other vendors price the same product (flagged "lowest" or "+x% vs lowest"). Search covers vendors and
   products.
5. **Products and components (E9)**: define a product (e.g. "48V LFP pack") and its components by adding
   them by hand or uploading an **Excel / CSV** list (columns such as "Component", "Qty", "Unit"). Each
   component gets a cost-driver template (rule-based, editable) and is matched to the closest item you buy
   in Zoho (editable), so its real vendor rates are tracked.
6. **Cost drivers (E3-lite)**: free market series (copper, aluminum, crude oil, USD/INR, CNY/INR) from
   Yahoo Finance with one click, plus CSV benchmark upload (`date,value`) for lithium, cobalt, nickel,
   steel, resin, epoxy raw materials, electronics and freight, which have no free source.
7. **Talking points (E7)**: for a matched component, the cost drivers' movement since your first purchase
   is turned into a justified price, the gap to what you pay, and talking points for the vendor. Numbers
   are computed in code; with an API key AI rewrites them, otherwise template text is used.
8. **AI features (E8, optional)**: "Generate components with AI", PDF component lists and AI-written
   talking points. They need `ANTHROPIC_API_KEY` in `.env`; without it they show "not configured".

9. **Price alerts (E2)**: flags (a) **rate increases**, a vendor's latest rate above its previous rate for
   the same product, and (b) **vendor gaps**, a vendor charging more than the cheapest vendor for the same
   product. Thresholds are adjustable (default 3% and 5%). Each alert shows the extra paid on the latest
   order, links to the rate history and talking points, and can be dismissed and restored. The nav shows the
   open-alert count. Alerts are in-app only; no email or digest yet.

10. **Talking points tab and vendor call brief**: the **Talking points** tab lists every vendor with the date of
    its last brief. A brief is generated **only when you click Generate/Regenerate**, then saved (with its
    earlier versions), so opening it later is instant, free and never calls the AI. A saved brief is flagged
    "out of date" when your Zoho data or market data has changed since. Each brief is ONE
    consolidated brief for a single call covering all the products bought from that vendor: the overall
    concern (spend-weighted gap vs cost drivers), the main product area (largest spend), the other products to
    raise (ranked by money at stake), products that are fine or have no market data, and one ask. It needs no
    manual matching: each Zoho item gets a cost-driver template from its name (a template set on a matched
    component overrides it). A product is only raised if its gap beats your rate-increase threshold, its rate
    rose, or another vendor sells it cheaper. Works even if only one product has market data. The same button
    appears on a vendor's page under Purchase rates. In the "All products" table the products to raise come
    first (largest overpayment first), each with a chart of the cost drivers' past price movement against
    your price, and hovering any "Drivers imply" price explains it step by step (starting price, each driver's
    move x its weight, total, and what is assumed unchanged).

The Zoho pull and everything above except the optional AI features is plain deterministic code.

### Known limitations
- Driver weights per template are starter estimates (see `app/catalog.py`), not validated against real BOMs.
- Only % change of a driver is used; copper/aluminum/crude are USD series, so INR-quoted items do not yet
  include the USD/INR effect unless that driver is in the template.
- CNY/INR from Yahoo currently returns only one data point.
- AI features (generate components, PDF component lists, talking points) were run once against the real API
  and worked; AI-suggested templates for borderline parts (insulation sheets, end plates) can be off, so
  review them. AI generates generic part names, so they often do not match your Zoho item names.

### Important: how "category" works
Zoho Books does not store a product category on items or bill lines (that exists in Zoho Inventory only).
The category shown for each product is therefore the **accounting account** each bill line was booked to
(for example "Raw Materials" or "Inventory Asset").

## Run it locally

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### One-time Zoho setup (you need this to log in with a real Zoho account)

1. Open the Zoho API Console for your data center (India: https://api-console.zoho.in,
   US: https://api-console.zoho.com) and choose **Add Client, then Server-based Applications**.
2. Set **Authorized Redirect URI** to exactly `http://localhost:5055/oauth/callback`.
3. Copy `.env.example` to `.env` and fill in `ZOHO_CLIENT_ID` and `ZOHO_CLIENT_SECRET`
   (and `ZOHO_DC` if you are not on India). **Never commit `.env`.**

### Start

```bash
.venv\Scripts\python.exe run.py
```

Open http://localhost:5055, log in with `123`, click **Connect Zoho Books**, sign in to Zoho and approve.
The sync runs in the background and the page shows progress.

### Tests

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests use a fake Zoho, so they need no network or credentials.

## Zoho details

- Scopes requested (read-only): `ZohoBooks.bills.READ`, `ZohoBooks.settings.READ`.
- The bills list does not include line items, so each bill is fetched individually.
  Zoho allows 100 API requests per minute per organization (daily limits depend on the plan, from 1,000 on
  Free), so the sync is throttled (`ZOHO_MIN_INTERVAL`) and retries on HTTP 429. Expect about 1 minute per
  85 bills.
- Draft and void bills are skipped. Bills in several currencies are never mixed in one comparison.
- Tokens are stored encrypted (Fernet) in `data/negotiator.db`. The key lives in `data/` (git-ignored).
- **Disconnect** revokes the token at Zoho and deletes the pulled data.

## Layout

```
app/            Flask app: routes, Zoho client + sync, grouping logic, templates
tests/          Unit and flow tests (fake Zoho)
docs/           Negotiator-Requirements-2-pager.docx: what is built today + enhancement backlog (editable)
mockup/         Static UI mockup of the full product
PLAN.md         Product plan
```
