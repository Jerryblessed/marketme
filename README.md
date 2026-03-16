# MarketMe — AI Marketing Agent Platform v2.0

> A Flask app powered by Amazon Nova 2 Lite (chat) and Nova 2 Sonic (voice).  
> AI agent that chats, sends campaigns, monitors email, scrapes leads, analyses images, and controls the UI.


## Project Structure

```
marketme/
│
├── app.py                  # Flask app factory — entry point
├── config.py               # All env-var config in one place
├── extensions.py           # Flask extensions (db, socketio, jwt, celery)
├── models.py               # SQLAlchemy models
├── tasks.py                # Celery background tasks
│
├── routes/
│   ├── __init__.py
│   ├── auth.py             # /api/auth/*  — register, login, miracle link
│   ├── business.py         # /api/business — create, settings, generate page
│   ├── campaigns.py        # /api/campaigns — CRUD + send
│   ├── chat.py             # /api/chat — AI agent (text + image)
│   ├── contacts.py         # /api/contacts — CRUD, import, CSV pool
│   ├── inbox.py            # /api/email-threads
│   ├── products.py         # /api/products — CRUD
│   └── public.py           # /biz/<slug> public page + SPA catch-all
│
├── sockets/
│   ├── __init__.py
│   └── events.py           # All SocketIO event handlers + voice bridge
│
├── utils/
│   ├── __init__.py
│   ├── csv_utils.py        # Shared CSV contacts pool helpers
│   ├── email_utils.py      # SMTP send + IMAP fetch
│   ├── intent_handler.py   # Maps AI intents → DB / Celery actions
│   ├── nova_utils.py       # Nova AI client (chat, page gen, email AI)
│   ├── scraper.py          # Playwright lead scraper
│   └── serializers.py      # Model → dict helpers for JSON responses
│
├── templates/
│   └── index.html          # Single-page app HTML (Tailwind + SocketIO)
│
├── static/
│   └── js/
│       └── app.js          # All frontend JavaScript
│
├── .env.example            # Copy to .env and fill in credentials
└── README.md
```

## Quick Start

```bash
# 1. Install dependencies
pip install flask flask-socketio flask-sqlalchemy flask-jwt-extended \
            celery[redis] redis openai websockets playwright \
            itsdangerous python-dotenv
playwright install chromium

# 2. Configure environment
cp .env.example .env
# Edit .env with your Nova API key, SMTP credentials, etc.

# 3. Start Redis (required for Celery)
redis-server (might not be needed if you have used celery and redis on windows)

# 4. Run the Flask app
python app.py

# 5. In a separate terminal — start the Celery worker
celery -A app:celery_app worker -l info --pool=solo
# 6. In a separate terminal — start the Celery beat scheduler
celery -A app:celery_app worker beat -l info
```

The app will be available at **http://localhost:5000**

## What runs where

| Process | Responsibility |
|---------|---------------|
| `python app.py` | Flask web server, REST API, SocketIO real-time events |
| `celery worker --beat` | IMAP inbox monitor (every 2 min), campaign sender (every 1 min), lead scraping, follow-up emails |

## Features

### 🤖 AI Agent (Nova 2 Lite)
- **Text chat** with full context history
- **Image analysis** — click 📎 to upload any image, agent describes and gives marketing insights
- **UI control** — say "show me contacts", "go to campaigns", "add a product", "dark mode"
- **Web grounding** — ask about competitors, markets, industry trends
- **Intent detection** — automatically triggers app actions from natural language

### 🎙️ Voice Agent (Nova 2 Sonic)
- Real-time bidirectional speech via WebSocket
- Auto-restarts before the 8-minute session limit
- Proper audio resampling to 24kHz regardless of browser sample rate

### 📧 Email System
- **Campaigns** — send to contacts + extra emails (type, paste, CSV upload)
- **IMAP monitoring** — checks inbox every 2 minutes via Celery Beat
- **Auto-reply** — AI drafts and sends replies to agreed/interested/question emails
- **Background** — campaigns keep sending even when browser is closed

### 👥 Contacts & Shared Pool
- **Shared CSV** (`shared_contacts.csv`) — ecosystem of contacts all users can access
- New contacts added via any method are appended to the CSV
- **Import** — type emails, paste comma-separated, upload CSV, or pick from shared pool
- **Find Leads** — Playwright headless browser searches Bing/DuckDuckGo, falls back to CSV pool

### 🎨 UI
- **Dark / Light mode** toggle (persistent, also agent-controllable)
- Chips-style multi-email input in campaigns and import
- Drag-and-drop CSV upload
- Real-time notifications for inbox, campaigns, leads

---

## Agent Commands (examples)
```
"Show me my products"           → navigates to Products panel
"Add a new product"             → opens Add Product modal
"Go to contacts"                → navigates to Contacts panel
"Find tech leads in Lagos"      → starts Playwright lead search
"Switch to light mode"          → toggles UI theme
"Launch a campaign for my CRM"  → drafts campaign email
"Analyse this image"            + 📎 upload → Nova 2 Lite image analysis
"Generate my business page"     → AI builds landing page at /biz/your-slug
```

---

## File Structure
```
app.py                 ← entire application (2100+ lines)
sample_contacts.csv    ← 20 seed contacts for the shared pool
shared_contacts.csv    ← auto-created, appended by the app
marketme.db            ← SQLite database (auto-created)
.env                   ← your configuration
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `NOVA_API_KEY` | ✅ | — | Amazon Nova API key |
| `SECRET_KEY` | ✅ | dev default | JWT signing key (32+ chars) |
| `SMTP_HOST` | ✅ | smtp.gmail.com | Outgoing mail server |
| `SMTP_PORT` | — | 587 | SMTP port |
| `SMTP_USER` | ✅ | — | Email username |
| `SMTP_PASS` | ✅ | — | Email password / app key |
| `IMAP_HOST` | — | imap.gmail.com | Inbox monitoring server |
| `IMAP_PORT` | — | 993 | IMAP SSL port |
| `REDIS_URL` | — | localhost:6379 | Celery broker |
| `DATABASE_URL` | — | sqlite:///marketme.db | Database |
| `APP_URL` | — | http://localhost:5000 | Public URL (for live chat) |
| `CSV_PATH` | — | shared_contacts.csv | Shared contacts file path |

---

## Production Notes
- Use `gunicorn` with `eventlet` worker instead of Flask dev server
- Set `SECRET_KEY` to a long random string
- Use PostgreSQL instead of SQLite for `DATABASE_URL`
- Use Redis Cloud or ElastiCache for `REDIS_URL`
- Run behind nginx with SSL for voice (requires HTTPS for microphone access)

```bash
# Production run example
gunicorn --worker-class eventlet -w 1 -b 0.0.0.0:5000 app:app
```
