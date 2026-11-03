import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("NEGOTIATOR_DATA_DIR") or ROOT / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _persisted(name, make):
    """Generate a secret once and keep it in the (git-ignored) data directory."""
    path = DATA_DIR / name
    if not path.exists():
        path.write_text(make())
    return path.read_text().strip()


SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or _persisted("flask_secret", lambda: secrets.token_hex(32))
FERNET_KEY = _persisted("fernet.key", lambda: Fernet.generate_key().decode())
DB_PATH = DATA_DIR / "negotiator.db"

ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "").strip()
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
ZOHO_DC = os.getenv("ZOHO_DC", "in").strip().lower()
ZOHO_REDIRECT_URI = os.getenv("ZOHO_REDIRECT_URI", "http://localhost:5055/oauth/callback").strip()
ZOHO_MIN_INTERVAL = float(os.getenv("ZOHO_MIN_INTERVAL", "0.7"))

AUTH_DEMO = os.getenv("AUTH_DEMO", "1") == "1"
DEMO_MOBILE = os.getenv("DEMO_MOBILE", "123").strip()

HISTORY_DAYS = 183  # "last 6 months"
