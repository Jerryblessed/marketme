from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db


class User(db.Model):
    __tablename__ = "users"
    id             = db.Column(db.Integer, primary_key=True)
    email          = db.Column(db.String(255), unique=True, nullable=False)
    name           = db.Column(db.String(255), default="")
    password_hash  = db.Column(db.String(512), nullable=True)
    role           = db.Column(db.String(50), default="owner")
    business_id    = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=True)
    miracle_token  = db.Column(db.String(512), nullable=True)
    miracle_expiry = db.Column(db.DateTime, nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash or "", pw)


class Business(db.Model):
    __tablename__ = "business"
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(255), nullable=False)
    slug         = db.Column(db.String(100), unique=True, nullable=False)
    tagline      = db.Column(db.String(500), default="")
    description  = db.Column(db.Text, default="")
    industry     = db.Column(db.String(100), default="")
    website      = db.Column(db.String(255), default="")
    page_html    = db.Column(db.Text, nullable=True)
    page_updated = db.Column(db.DateTime, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)


class Product(db.Model):
    __tablename__ = "products"
    id          = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=False)
    name        = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, default="")
    price       = db.Column(db.Float, default=0.0)
    currency    = db.Column(db.String(10), default="USD")
    image_url   = db.Column(db.String(500), default="")
    category    = db.Column(db.String(100), default="")
    active      = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class Contact(db.Model):
    __tablename__ = "contacts"
    id          = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=False)
    name        = db.Column(db.String(255), default="")
    email       = db.Column(db.String(255), nullable=False)
    company     = db.Column(db.String(255), default="")
    phone       = db.Column(db.String(50), default="")
    source      = db.Column(db.String(50), default="manual")
    status      = db.Column(db.String(50), default="new")
    notes       = db.Column(db.Text, default="")
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class Campaign(db.Model):
    __tablename__ = "campaigns"
    id          = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=False)
    name        = db.Column(db.String(255), default="")
    subject     = db.Column(db.String(500), default="")
    body_html   = db.Column(db.Text, default="")
    body_plain  = db.Column(db.Text, default="")
    status      = db.Column(db.String(50), default="draft")
    scheduled_at = db.Column(db.DateTime, nullable=True)
    sent_at     = db.Column(db.DateTime, nullable=True)
    sent_count  = db.Column(db.Integer, default=0)
    contact_ids = db.Column(db.Text, default="[]")
    raw_emails  = db.Column(db.Text, default="[]")   # extra emails not in contacts
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class EmailThread(db.Model):
    __tablename__ = "email_threads"
    id           = db.Column(db.Integer, primary_key=True)
    business_id  = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=False)
    contact_id   = db.Column(db.Integer, db.ForeignKey("contacts.id"), nullable=True)
    campaign_id  = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=True)
    message_id   = db.Column(db.String(500), default="")
    in_reply_to  = db.Column(db.String(500), default="")
    subject      = db.Column(db.String(500), default="")
    from_email   = db.Column(db.String(255), default="")
    body_snippet = db.Column(db.Text, default="")
    direction    = db.Column(db.String(10), default="inbound")
    intent       = db.Column(db.String(50), default="other")
    ai_auto_reply = db.Column(db.Boolean, default=False)
    ai_reply_body = db.Column(db.Text, default="")
    received_at  = db.Column(db.DateTime, default=datetime.utcnow)


class ChatLog(db.Model):
    __tablename__ = "chat_logs"
    id              = db.Column(db.Integer, primary_key=True)
    business_id     = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=True)
    session_id      = db.Column(db.String(100), default="")
    role            = db.Column(db.String(20), default="user")
    content         = db.Column(db.Text, default="")
    intent_detected = db.Column(db.String(100), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


class LiveChatRoom(db.Model):
    __tablename__ = "live_chat_rooms"
    id             = db.Column(db.Integer, primary_key=True)
    business_id    = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=False)
    room_id        = db.Column(db.String(100), unique=True)
    customer_name  = db.Column(db.String(255), default="Guest")
    customer_email = db.Column(db.String(255), default="")
    status         = db.Column(db.String(50), default="waiting")
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
