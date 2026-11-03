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
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
