#!/usr/bin/env python3
"""
MarketMe — AI Marketing Agent Platform  v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Run:   python app.py
Agent: celery -A app:celery_app worker --beat -l info

pip install flask flask-socketio flask-sqlalchemy flask-jwt-extended
           celery[redis] redis openai websockets playwright
           itsdangerous python-dotenv

playwright install chromium
"""

import sys
import logging
from datetime import timedelta

try:
    from flask import Flask
    from flask_jwt_extended import JWTManager
except ImportError as e:
    sys.exit(f"Missing dependency: {e}")

from config import SECRET_KEY, DATABASE_URL, REDIS_URL
from extensions import db, socketio, jwt, celery_app
from utils.csv_utils import csv_ensure

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("marketme")


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=SECRET_KEY,
        SQLALCHEMY_DATABASE_URI=DATABASE_URL,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=SECRET_KEY,
        JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=12),
        JWT_REFRESH_TOKEN_EXPIRES=timedelta(days=30),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )

    # ── Init extensions ───────────────────────────────────────────
    db.init_app(app)
    jwt.init_app(app)
    socketio.init_app(app, cors_allowed_origins="*", async_mode="threading", logger=False)

    # ── Register blueprints ───────────────────────────────────────
    from routes.auth      import auth_bp
    from routes.business  import business_bp
    from routes.products  import products_bp
    from routes.contacts  import contacts_bp
    from routes.campaigns import campaigns_bp
    from routes.inbox     import inbox_bp
    from routes.chat      import chat_bp
    from routes.public    import public_bp

    for bp in (auth_bp, business_bp, products_bp, contacts_bp,
               campaigns_bp, inbox_bp, chat_bp, public_bp):
        app.register_blueprint(bp)

    # ── Register SocketIO events ──────────────────────────────────
    from sockets.events import register_socket_events
    register_socket_events(socketio)

    return app


# ── Application instance (used by Celery tasks too) ───────────────
app = create_app()

# Re-export celery_app so Celery CLI can find it:
#   celery -A app:celery_app worker --beat -l info
__all__ = ["app", "celery_app"]


if __name__ == "__main__":
    csv_ensure()
    with app.app_context():
        from models import (User, Business, Product, Contact,
                            Campaign, EmailThread, ChatLog, LiveChatRoom)
        db.create_all()
        log.info("✦ MarketMe v2.0 DB initialised")

    log.info("✦ Starting on http://0.0.0.0:5000")
    log.info("✦ Agent: celery -A app:celery_app worker --beat -l info")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
