from dotenv import load_dotenv
load_dotenv()

import os
from pathlib import Path

NOVA_API_KEY  = os.getenv("NOVA_API_KEY", "")
SECRET_KEY    = os.getenv("SECRET_KEY", "SECRET_KEY=marketme-production-secret-key-2026-abc")
REDIS_URL     = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL  = os.getenv("DATABASE_URL", "sqlite:///marketme.db")
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", 587))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASS     = os.getenv("SMTP_PASS", "")
SMTP_FROM     = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", ""))
IMAP_HOST     = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT     = int(os.getenv("IMAP_PORT", 993))
APP_URL       = os.getenv("APP_URL", "http://localhost:5000")
NOVA_BASE_URL = "https://api.nova.amazon.com/v1"
NOVA_WS_URL   = "wss://api.nova.amazon.com/v1/realtime?model=nova-2-sonic-v1"
CSV_PATH      = Path(os.getenv("CSV_PATH", "shared_contacts.csv"))
