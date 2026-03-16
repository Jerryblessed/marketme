from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from flask_jwt_extended import JWTManager
from celery import Celery
from celery.schedules import crontab
from itsdangerous import URLSafeTimedSerializer

from config import SECRET_KEY, REDIS_URL

db         = SQLAlchemy()
socketio   = SocketIO()
jwt        = JWTManager()
signer     = URLSafeTimedSerializer(SECRET_KEY)

celery_app = Celery("marketme", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    timezone="UTC",
    beat_schedule={
        "imap-monitor":      {"task": "marketme.monitor_inbox",     "schedule": crontab(minute="*/2")},
        "process-campaigns": {"task": "marketme.process_campaigns", "schedule": crontab(minute="*/1")},
    },
)
