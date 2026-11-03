import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    mobile TEXT PRIMARY KEY,
    dc TEXT NOT NULL,
    accounts_url TEXT NOT NULL,
    api_domain TEXT NOT NULL,
    org_id TEXT NOT NULL,
    org_name TEXT,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at REAL NOT NULL,
    connected_at TEXT NOT NULL,
    last_sync_at TEXT,
    last_sync_status TEXT
);
CREATE TABLE IF NOT EXISTS bill_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mobile TEXT NOT NULL,
    bill_id TEXT NOT NULL,
    bill_number TEXT,
    bill_date TEXT NOT NULL,
    status TEXT,
    vendor_id TEXT,
    vendor_name TEXT,
    currency TEXT,
    item_id TEXT,
    item_name TEXT,
    sku TEXT,
    account_name TEXT,
    product_type TEXT,
    unit TEXT,
    rate REAL NOT NULL,
    quantity REAL
);
CREATE INDEX IF NOT EXISTS idx_lines_mobile ON bill_lines(mobile);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mobile TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    mobile TEXT NOT NULL,
    name TEXT NOT NULL,
    qty REAL NOT NULL DEFAULT 1,
    unit TEXT,
    template TEXT NOT NULL DEFAULT 'other',
    source TEXT NOT NULL,           -- manual | upload | ai
    item_key TEXT,                  -- matched Zoho product key (item id | currency)
    match_score REAL
);
CREATE INDEX IF NOT EXISTS idx_components_product ON components(product_id);
CREATE TABLE IF NOT EXISTS alert_settings (
    mobile TEXT PRIMARY KEY,
    increase_pct REAL NOT NULL DEFAULT 3,   -- flag a rate increase of at least this much vs the previous purchase
    gap_pct REAL NOT NULL DEFAULT 5         -- flag a vendor this much above the cheapest vendor for the same product
);
CREATE TABLE IF NOT EXISTS alert_dismissed (
    mobile TEXT NOT NULL,
    key TEXT NOT NULL,
    dismissed_at TEXT NOT NULL,
    PRIMARY KEY (mobile, key)
);
CREATE TABLE IF NOT EXISTS driver_points (
    driver TEXT NOT NULL,
    mobile TEXT NOT NULL DEFAULT '',  -- '' = shared public series, otherwise the user's own benchmark
    date TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (driver, mobile, date)
);
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
