# Negotiator

Helps battery/EV manufacturers renegotiate vendor prices. See [PLAN.md](PLAN.md) for the product plan and
`mockup/index.html` for the UI mockup.

## What works today

1. **Login** with a mobile number (demo number `123`; real OTP login is not built yet).
2. **Connect page** with a Zoho Books connector. It lists the read-only permissions first, then sends the
   user to Zoho's own sign-in and consent page (OAuth 2.0). Negotiator never sees the Zoho password.
3. **Bills sync**: pulls the last ~6 months of Zoho Books **bills** and their line items.
4. **Purchase rates page**: a vendor tree on the left, with each vendor expanding into its products.
   Selecting a vendor shows all its products with purchase rates (latest, average weighted by quantity,
   min to max, change, trend). Selecting a product shows its rate history chart, every purchase, and how
   other vendors price the same product (flagged "lowest" or "+x% vs lowest"). Search covers vendors and
   products.

The Zoho pull is plain deterministic code. No AI runs on the server.

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
