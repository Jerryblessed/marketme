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
from dotenv import load_dotenv
load_dotenv()

import os, sys, json, uuid, ssl, threading, asyncio, logging, re, csv, io
import imaplib, email as email_lib, smtplib, base64
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header
from pathlib import Path

try:
    from flask import Flask, request, jsonify, render_template_string
    from flask_socketio import SocketIO, emit, join_room
    from flask_sqlalchemy import SQLAlchemy
    from flask_jwt_extended import (JWTManager, create_access_token,
        create_refresh_token, jwt_required, get_jwt_identity)
    from celery import Celery
    from celery.schedules import crontab
    from werkzeug.security import generate_password_hash, check_password_hash
    from itsdangerous import URLSafeTimedSerializer
    from openai import OpenAI
    import websockets as ws_lib
except ImportError as e:
    sys.exit(f"Missing: {e}")

# ── Config ────────────────────────────────────────────────────────
NOVA_API_KEY  = os.getenv("NOVA_API_KEY","")
SECRET_KEY    = os.getenv("SECRET_KEY","dev-marketme-secret-key-change-in-prod")
REDIS_URL     = os.getenv("REDIS_URL","redis://localhost:6379/0")
DATABASE_URL  = os.getenv("DATABASE_URL","sqlite:///marketme.db")
SMTP_HOST     = os.getenv("SMTP_HOST","smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT",587))
SMTP_USER     = os.getenv("SMTP_USER","")
SMTP_PASS     = os.getenv("SMTP_PASS","")
SMTP_FROM     = os.getenv("SMTP_FROM", SMTP_USER)
IMAP_HOST     = os.getenv("IMAP_HOST","imap.gmail.com")
IMAP_PORT     = int(os.getenv("IMAP_PORT",993))
APP_URL       = os.getenv("APP_URL","http://localhost:5000")
NOVA_BASE_URL = "https://api.nova.amazon.com/v1"
NOVA_WS_URL   = "wss://api.nova.amazon.com/v1/realtime?model=nova-2-sonic-v1"
CSV_PATH      = Path(os.getenv("CSV_PATH","shared_contacts.csv"))

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
log = logging.getLogger("marketme")

app = Flask(__name__)
app.config.update(SECRET_KEY=SECRET_KEY, SQLALCHEMY_DATABASE_URI=DATABASE_URL,
    SQLALCHEMY_TRACK_MODIFICATIONS=False, JWT_SECRET_KEY=SECRET_KEY,
    JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=12),
    JWT_REFRESH_TOKEN_EXPIRES=timedelta(days=30),
    MAX_CONTENT_LENGTH=16*1024*1024)

db       = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", logger=False)
jwt      = JWTManager(app)
signer   = URLSafeTimedSerializer(SECRET_KEY)

celery_app = Celery("marketme", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(timezone="UTC", beat_schedule={
    "imap-monitor":      {"task":"marketme.monitor_inbox",     "schedule":crontab(minute="*/2")},
    "process-campaigns": {"task":"marketme.process_campaigns", "schedule":crontab(minute="*/1")},
})

# ── MODELS ────────────────────────────────────────────────────────
class User(db.Model):
    __tablename__="users"
    id=db.Column(db.Integer,primary_key=True)
    email=db.Column(db.String(255),unique=True,nullable=False)
    name=db.Column(db.String(255),default="")
    password_hash=db.Column(db.String(512),nullable=True)
    role=db.Column(db.String(50),default="owner")
    business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=True)
    miracle_token=db.Column(db.String(512),nullable=True)
    miracle_expiry=db.Column(db.DateTime,nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.utcnow)
    def set_password(self,pw): self.password_hash=generate_password_hash(pw)
    def check_password(self,pw): return check_password_hash(self.password_hash or "",pw)

class Business(db.Model):
    __tablename__="business"
    id=db.Column(db.Integer,primary_key=True)
    name=db.Column(db.String(255),nullable=False)
    slug=db.Column(db.String(100),unique=True,nullable=False)
    tagline=db.Column(db.String(500),default="")
    description=db.Column(db.Text,default="")
    industry=db.Column(db.String(100),default="")
    website=db.Column(db.String(255),default="")
    page_html=db.Column(db.Text,nullable=True)
    page_updated=db.Column(db.DateTime,nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.utcnow)

class Product(db.Model):
    __tablename__="products"
    id=db.Column(db.Integer,primary_key=True)
    business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False)
    name=db.Column(db.String(255),nullable=False)
    description=db.Column(db.Text,default="")
    price=db.Column(db.Float,default=0.0)
    currency=db.Column(db.String(10),default="USD")
    image_url=db.Column(db.String(500),default="")
    category=db.Column(db.String(100),default="")
    active=db.Column(db.Boolean,default=True)
    created_at=db.Column(db.DateTime,default=datetime.utcnow)

class Contact(db.Model):
    __tablename__="contacts"
    id=db.Column(db.Integer,primary_key=True)
    business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False)
    name=db.Column(db.String(255),default="")
    email=db.Column(db.String(255),nullable=False)
    company=db.Column(db.String(255),default="")
    phone=db.Column(db.String(50),default="")
    source=db.Column(db.String(50),default="manual")
    status=db.Column(db.String(50),default="new")
    notes=db.Column(db.Text,default="")
    created_at=db.Column(db.DateTime,default=datetime.utcnow)

class Campaign(db.Model):
    __tablename__="campaigns"
    id=db.Column(db.Integer,primary_key=True)
    business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False)
    name=db.Column(db.String(255),default="")
    subject=db.Column(db.String(500),default="")
    body_html=db.Column(db.Text,default="")
    body_plain=db.Column(db.Text,default="")
    status=db.Column(db.String(50),default="draft")
    scheduled_at=db.Column(db.DateTime,nullable=True)
    sent_at=db.Column(db.DateTime,nullable=True)
    sent_count=db.Column(db.Integer,default=0)
    contact_ids=db.Column(db.Text,default="[]")
    raw_emails=db.Column(db.Text,default="[]")  # extra emails not in contacts
    created_at=db.Column(db.DateTime,default=datetime.utcnow)

class EmailThread(db.Model):
    __tablename__="email_threads"
    id=db.Column(db.Integer,primary_key=True)
    business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False)
    contact_id=db.Column(db.Integer,db.ForeignKey("contacts.id"),nullable=True)
    campaign_id=db.Column(db.Integer,db.ForeignKey("campaigns.id"),nullable=True)
    message_id=db.Column(db.String(500),default="")
    in_reply_to=db.Column(db.String(500),default="")
    subject=db.Column(db.String(500),default="")
    from_email=db.Column(db.String(255),default="")
    body_snippet=db.Column(db.Text,default="")
    direction=db.Column(db.String(10),default="inbound")
    intent=db.Column(db.String(50),default="other")
    ai_auto_reply=db.Column(db.Boolean,default=False)
    ai_reply_body=db.Column(db.Text,default="")
    received_at=db.Column(db.DateTime,default=datetime.utcnow)

class ChatLog(db.Model):
    __tablename__="chat_logs"
    id=db.Column(db.Integer,primary_key=True)
    business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=True)
    session_id=db.Column(db.String(100),default="")
    role=db.Column(db.String(20),default="user")
    content=db.Column(db.Text,default="")
    intent_detected=db.Column(db.String(100),nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.utcnow)

class LiveChatRoom(db.Model):
    __tablename__="live_chat_rooms"
    id=db.Column(db.Integer,primary_key=True)
    business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False)
    room_id=db.Column(db.String(100),unique=True)
    customer_name=db.Column(db.String(255),default="Guest")
    customer_email=db.Column(db.String(255),default="")
    status=db.Column(db.String(50),default="waiting")
    created_at=db.Column(db.DateTime,default=datetime.utcnow)

# ── CSV SHARED CONTACTS ───────────────────────────────────────────
def csv_ensure():
    if not CSV_PATH.exists():
        seed = Path("sample_contacts.csv")
        if seed.exists():
            import shutil; shutil.copy(seed, CSV_PATH)
        else:
            with open(CSV_PATH,"w",newline="") as f:
                csv.DictWriter(f,fieldnames=["name","email","company","phone","notes"]).writeheader()

def csv_load():
    csv_ensure()
    rows=[]
    with open(CSV_PATH,newline="") as f:
        for r in csv.DictReader(f): rows.append(r)
    return rows

def csv_append(name,email,company="",phone="",notes=""):
    csv_ensure()
    existing=[r["email"].lower() for r in csv_load()]
    if email.lower() in existing: return False
    with open(CSV_PATH,"a",newline="") as f:
        csv.DictWriter(f,fieldnames=["name","email","company","phone","notes"]).writerow(
            {"name":name,"email":email,"company":company,"phone":phone,"notes":notes})
    return True

# ── NOVA CLIENT ───────────────────────────────────────────────────
def nova_client():
    return OpenAI(api_key=NOVA_API_KEY, base_url=NOVA_BASE_URL)

AGENT_SYSTEM="""You are MarketMe Agent — an expert AI marketing assistant with FULL CONTROL of the app.

When you detect an intent, append ONE JSON block at the END of your reply on its own line:
{"intent": "<intent>", "params": {}}

APP NAVIGATION INTENTS (do these when user asks to go somewhere):
- navigate        → params: {panel: "chat|voice|products|contacts|campaigns|inbox|livechats|settings"}
- open_modal      → params: {modal: "add-product|add-contact|add-campaign|find-leads"}
- toggle_theme    → params: {mode: "light|dark"}
- show_notification → params: {title: "...", message: "..."}

BUSINESS ACTION INTENTS:
- add_product     → params: {name, description, price, category}
- launch_campaign → params: {campaign_name, product_name, tone, target_audience}
- find_leads      → params: {industry, location, keywords}
- schedule_followup → params: {contact_email, delay_hours, message_hint}
- connect_customer → params: {contact_email}
- generate_page   → params: {style_hint}

RULES:
- If user says "show me products" or "go to contacts" → use navigate
- If user says "add a product" → use open_modal with modal="add-product"
- If user says "dark mode" or "light mode" → use toggle_theme
- If user asks about markets/competitors → use web grounding
- Images: describe what you see and relate it to business/marketing context
- Only emit JSON when intent is clearly present"""

def chat_with_nova(messages, biz=None, image_b64=None, image_mime="image/jpeg"):
    client = nova_client()
    sys_content = AGENT_SYSTEM
    if biz:
        sys_content += f"\n\nBusiness: {biz.name} | Industry: {biz.industry} | {biz.description or ''}"
    full_msgs = [{"role":"system","content":sys_content}]
    # Build messages, inject image into last user message if present
    for i, msg in enumerate(messages):
        if i == len(messages)-1 and image_b64:
            full_msgs.append({"role":"user","content":[
                {"type":"text","text": msg.get("content","Analyze this image")},
                {"type":"image_url","image_url":{"url":f"data:{image_mime};base64,{image_b64}"}}
            ]})
        else:
            full_msgs.append(msg)
    try:
        resp = client.chat.completions.create(
            model="nova-2-lite-v1", messages=full_msgs,
            max_tokens=1000, temperature=0.7,
            extra_body={"system_tools":["nova_grounding"]} if not any(
                kw in (messages[-1].get("content","") if messages else "").lower()
                for kw in ["go to","show me","open","navigate","dark mode","light mode","switch to","add a"]
            ) else {}
            )
        raw = resp.choices[0].message.content or ""
        intent, params, content = None, {}, raw
        m = re.search(r'\{"intent"\s*:[^{}]+(?:"params"\s*:\s*\{[^{}]*\})?\s*\}', raw, re.DOTALL)
        if m:
            try:
                data=json.loads(m.group()); intent=data.get("intent"); params=data.get("params",{})
                content=raw[:m.start()].strip()
            except: pass
        return {"content":content,"intent":intent,"params":params}
    except Exception as e:
        log.error(f"Nova chat: {e}")
        return {"content":f"I ran into an issue: {e}","intent":None,"params":{}}

def generate_business_page(biz, products):
    client = nova_client()
    prods = "\n".join([f"- {p.name}: {p.description} (${p.price} {p.currency})" for p in products]) or "No products yet."
    prompt=(f"Create complete modern HTML business landing page.\nBusiness:{biz.name}\nTagline:{biz.tagline}\n"
            f"Description:{biz.description}\nIndustry:{biz.industry}\nProducts:\n{prods}\n\n"
            f"- Full HTML with Tailwind CDN - Professional design - Sections: hero,about,products,CTA\n"
            f"- Chat button calling window.openLiveChat() - Mobile responsive\n- Return ONLY raw HTML")
    try:
        resp=client.chat.completions.create(model="nova-2-lite-v1",
            messages=[{"role":"user","content":prompt}],max_tokens=3000)
        return re.sub(r"^```html\n?","",(resp.choices[0].message.content or "")).rstrip("`").strip()
    except Exception as e:
        return f"<html><body><h1>{biz.name}</h1><p>Error: {e}</p></body></html>"

def classify_email_intent(subject, body):
    try:
        resp=nova_client().chat.completions.create(model="nova-2-lite-v1",max_tokens=5,temperature=0,
            messages=[{"role":"user","content":f"ONE word only: agreed/declined/interested/question/other\nSubject:{subject}\nBody:{body[:300]}"}])
        w=resp.choices[0].message.content.strip().lower()
        return w if w in("agreed","declined","interested","question") else "other"
    except: return "other"

def draft_auto_reply(subject, body, biz_name, contact_name=""):
    try:
        resp=nova_client().chat.completions.create(model="nova-2-lite-v1",max_tokens=300,
            messages=[{"role":"user","content":
                f"As marketing agent for {biz_name}, write a warm professional reply to {contact_name or 'this customer'}.\n"
                f"Subject:{subject}\nThey wrote:{body[:400]}\nReturn ONLY the reply body text, no subject line."}])
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"auto_reply draft: {e}"); return ""

def draft_campaign_email(biz, params):
    try:
        prompt=(f"Draft marketing email for {biz.name}.\nCampaign:{params.get('campaign_name','')}\n"
                f"Product:{params.get('product_name','')}\nTone:{params.get('tone','professional')}\n"
                f"Target:{params.get('target_audience','general')}\n"
                f'Return ONLY JSON: {{"subject":"...","body_plain":"...","body_html":"..."}}')
        resp=nova_client().chat.completions.create(model="nova-2-lite-v1",max_tokens=1000,
            messages=[{"role":"user","content":prompt}])
        return json.loads(re.sub(r"^```json\n?","",resp.choices[0].message.content or "").rstrip("`").strip())
    except:
        return {"subject":f"News from {biz.name}","body_plain":f"Hi,\n\nWe have exciting news.\n\nBest,\n{biz.name}","body_html":""}

# ── SMTP / IMAP ───────────────────────────────────────────────────
def smtp_send(to, subject, body_plain, body_html="", reply_to=None):
    """Always uses system SMTP from .env"""
    if not SMTP_HOST or not SMTP_USER:
        log.warning("SMTP not configured in .env"); return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = SMTP_FROM or SMTP_USER
        msg["To"]      = to
        msg["Subject"] = subject
        if reply_to: msg["Reply-To"] = reply_to
        msg.attach(MIMEText(body_plain,"plain"))
        if body_html: msg.attach(MIMEText(body_html,"html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.ehlo(); s.starttls(); s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, to, msg.as_string())
        log.info(f"Email sent → {to} | {subject}")
        return True
    except Exception as e:
        log.error(f"SMTP error: {e}"); return False

def smtp_send_many(to_list, subject, body_plain, body_html=""):
    """Batch send, returns count"""
    sent = 0
    for to in to_list:
        if smtp_send(to, subject, body_plain, body_html): sent += 1
    return sent

def fetch_unseen_emails():
    """Fetch from system IMAP (.env credentials)"""
    if not SMTP_USER:
        return []
    msgs = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(SMTP_USER, SMTP_PASS)
        mail.select("INBOX")
        _, data = mail.search(None, "UNSEEN")
        nums = data[0].split()
        log.info(f"IMAP: {len(nums)} unseen emails")
        for num in nums[-30:]:
            _, raw = mail.fetch(num, "(RFC822)")
            if not raw or not raw[0]: continue
            msg = email_lib.message_from_bytes(raw[0][1])
            sp = decode_header(msg.get("Subject",""))[0]
            subject = sp[0].decode(sp[1] or "utf-8") if isinstance(sp[0],bytes) else str(sp[0])
            from_hdr = msg.get("From","")
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type()=="text/plain":
                        body = part.get_payload(decode=True).decode("utf-8",errors="replace"); break
            else:
                body = msg.get_payload(decode=True).decode("utf-8",errors="replace")
            msgs.append({"message_id":msg.get("Message-ID",""),
                         "in_reply_to":msg.get("In-Reply-To",""),
                         "subject":subject,"from":from_hdr,"body":body[:800]})
        mail.logout()
    except Exception as e:
        log.error(f"IMAP: {e}")
    return msgs

# ── PLAYWRIGHT ─────────────────────────────────────────────────────
def scrape_leads(industry, location, keywords, limit=20):
    leads = []
    query = f"{keywords or industry} {location} business contact email"
    log.info(f"Playwright scraping: {query}")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox","--disable-setuid-sandbox",
                      "--disable-blink-features=AutomationControlled",
                      "--disable-dev-shm-usage"])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                viewport={"width":1280,"height":720})
            page = ctx.new_page()
            page.set_extra_http_headers({"Accept-Language":"en-US,en;q=0.9"})

            # Try Bing first (less blocking than Google)
            try:
                page.goto(f"https://www.bing.com/search?q={query.replace(' ','+')}",
                          timeout=25000, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                content = page.content()
                # Also try clicking more results
                page.keyboard.press("End")
                page.wait_for_timeout(1000)
                content += page.content()
            except Exception as be:
                log.warning(f"Bing failed ({be}), trying DDG")
                page.goto(f"https://duckduckgo.com/?q={query.replace(' ','+')}",
                          timeout=25000, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                content = page.content()

            # Extract emails from page
            found_emails = list(set(re.findall(r'[\w.+\-]{2,}@[\w\-]+\.\w{2,6}', content)))
            skip = {"noreply","no-reply","example","test","privacy","contact@bing",
                    "support@microsoft","feedback","postmaster","webmaster"}
            for email in found_emails[:limit]:
                if any(s in email.lower() for s in skip): continue
                domain = email.split("@")[1].split(".")[0]
                name   = email.split("@")[0].replace("."," ").replace("_"," ").title()
                leads.append({"name":name,"company":domain.title(),"email":email,
                               "notes":f"Scraped: {industry} {location}"})

            # Also grab from CSV shared pool and use as seed leads for demo
            if len(leads) < 5:
                for row in csv_load():
                    if industry.lower() in (row.get("notes","")+" "+row.get("company","")).lower() \
                       or location.lower() in (row.get("notes","")+" "+row.get("company","")).lower():
                        leads.append({"name":row["name"],"company":row.get("company",""),
                                      "email":row["email"],"notes":row.get("notes","")})
                    if len(leads) >= limit: break

            browser.close()
            log.info(f"Playwright found {len(leads)} leads")
    except Exception as e:
        log.error(f"Playwright error: {e}")
        # Fallback: return from CSV if playwright totally fails
        for row in csv_load()[:limit]:
            leads.append({"name":row["name"],"company":row.get("company",""),
                          "email":row["email"],"notes":row.get("notes","")})
    return leads

# ── CELERY TASKS ──────────────────────────────────────────────────
@celery_app.task(name="marketme.monitor_inbox")
def monitor_inbox():
    with app.app_context():
        bizs = Business.query.all()
        if not bizs: return
        emails = fetch_unseen_emails()
        log.info(f"Processing {len(emails)} emails for {len(bizs)} businesses")
        for em in emails:
            from_addr = (re.findall(r"[\w.+\-]+@[\w\-]+\.\w{2,6}", em["from"]) or [em["from"]])[0]
            # Skip our own outbound
            if from_addr.lower() == SMTP_USER.lower(): continue
            # Find which business this email relates to (via contact lookup)
            for biz in bizs:
                if EmailThread.query.filter_by(message_id=em["message_id"],business_id=biz.id).first():
                    continue
                intent  = classify_email_intent(em["subject"], em["body"])
                contact = Contact.query.filter_by(business_id=biz.id,email=from_addr).first()
                # Update contact status
                sm = {"agreed":"agreed","declined":"declined","interested":"interested","question":"contacted"}
                if contact and intent in sm:
                    contact.status = sm[intent]
                # Auto-reply — always reply to interested/agreed/question
                ai_reply = ""
                if intent in ("agreed","interested","question","other"):
                    cname = contact.name if contact else from_addr
                    ai_reply = draft_auto_reply(em["subject"], em["body"], biz.name, cname)
                    if ai_reply:
                        ok = smtp_send(from_addr, f"Re: {em['subject']}", ai_reply)
                        log.info(f"Auto-reply to {from_addr}: {'sent' if ok else 'failed'}")
                        # Append to CSV ecosystem
                        csv_append(cname, from_addr, notes=f"Replied via MarketMe - intent:{intent}")

                thread = EmailThread(business_id=biz.id,
                    contact_id=contact.id if contact else None,
                    message_id=em["message_id"],in_reply_to=em.get("in_reply_to",""),
                    subject=em["subject"],from_email=from_addr,
                    body_snippet=em["body"][:500],direction="inbound",
                    intent=intent,ai_auto_reply=bool(ai_reply),ai_reply_body=ai_reply)
                db.session.add(thread); db.session.commit()
                socketio.emit("inbox_update",{
                    "business_id":biz.id,"from":from_addr,"subject":em["subject"],
                    "intent":intent,"ai_replied":bool(ai_reply)},room=f"biz_{biz.id}")
                break  # one business per email

@celery_app.task(name="marketme.process_campaigns")
def process_campaigns():
    with app.app_context():
        now = datetime.utcnow()
        for cp in Campaign.query.filter(Campaign.status=="scheduled",
                                        Campaign.scheduled_at<=now).all():
            cp.status="sending"; db.session.commit()
            biz   = db.session.get(Business, cp.business_id)
            sent  = 0
            # Send to DB contacts
            for cid in json.loads(cp.contact_ids or "[]"):
                c = db.session.get(Contact, cid)
                if not c or not c.email: continue
                if smtp_send(c.email, cp.subject, cp.body_plain, cp.body_html):
                    sent += 1; c.status = "contacted"
                    db.session.add(EmailThread(business_id=biz.id,contact_id=c.id,
                        campaign_id=cp.id,subject=cp.subject,from_email=SMTP_USER,
                        body_snippet=cp.body_plain[:300],direction="outbound"))
            # Send to raw emails (CSV/typed)
            for email in json.loads(cp.raw_emails or "[]"):
                if smtp_send(email, cp.subject, cp.body_plain, cp.body_html):
                    sent += 1
                    csv_append("", email, notes="Campaign recipient")
            cp.status="sent"; cp.sent_at=datetime.utcnow(); cp.sent_count=sent
            db.session.commit()
            socketio.emit("campaign_update",{"campaign_id":cp.id,"name":cp.name,
                "sent":sent,"status":"sent"},room=f"biz_{biz.id}")

@celery_app.task(name="marketme.scrape_leads_task")
def scrape_leads_task(business_id, industry, location, keywords):
    with app.app_context():
        leads = scrape_leads(industry, location, keywords)
        added = 0
        for lead in leads:
            if not Contact.query.filter_by(business_id=business_id,email=lead["email"]).first():
                db.session.add(Contact(business_id=business_id,email=lead["email"],
                    name=lead.get("name",""),company=lead.get("company",""),
                    notes=lead.get("notes",""),source="scrape"))
                csv_append(lead.get("name",""), lead["email"],
                           company=lead.get("company",""), notes=lead.get("notes",""))
                added += 1
        db.session.commit()
        socketio.emit("leads_found",{"count":added,"industry":industry,"location":location},
                      room=f"biz_{business_id}")

@celery_app.task(name="marketme.send_followup_email")
def send_followup_email_task(business_id, contact_email, message_hint):
    with app.app_context():
        biz = db.session.get(Business, business_id)
        if not biz: return
        try:
            resp=nova_client().chat.completions.create(model="nova-2-lite-v1",max_tokens=400,
                messages=[{"role":"user","content":
                    f"Follow-up email for {biz.name}. Hint: {message_hint}. "
                    f'Return ONLY JSON: {{"subject":"...","body":"..."}}'}])
            data=json.loads(re.sub(r"^```json\n?","",resp.choices[0].message.content or "").rstrip("`").strip())
            smtp_send(contact_email, data["subject"], data["body"])
        except Exception as e: log.error(f"Follow-up: {e}")

# ── INTENT HANDLER ────────────────────────────────────────────────
def handle_intent(intent, params, user, biz):
    # UI intents (handled frontend, just pass through)
    if intent in ("navigate","open_modal","toggle_theme","show_notification"):
        return {"type": intent, **params}

    if intent=="add_product":
        p=Product(business_id=biz.id,name=params.get("name","New Product"),
            description=params.get("description",""),price=float(params.get("price",0)),
            category=params.get("category",""))
        db.session.add(p); db.session.commit()
        return {"type":"product_added","product_id":p.id,"name":p.name}

    if intent=="launch_campaign":
        draft=draft_campaign_email(biz,params)
        contacts_all=Contact.query.filter_by(business_id=biz.id).all()
        cp=Campaign(business_id=biz.id,
            name=params.get("campaign_name",f"Campaign {datetime.utcnow().date()}"),
            subject=draft.get("subject",""),body_html=draft.get("body_html",""),
            body_plain=draft.get("body_plain",""),
            contact_ids=json.dumps([c.id for c in contacts_all]),status="draft")
        db.session.add(cp); db.session.commit()
        return {"type":"campaign_drafted","campaign_id":cp.id,"name":cp.name}

    if intent=="find_leads":
        scrape_leads_task.delay(biz.id,params.get("industry",biz.industry or ""),
                                params.get("location",""),params.get("keywords",""))
        return {"type":"lead_search_started"}

    if intent=="schedule_followup":
        dh=float(params.get("delay_hours",24))
        send_followup_email_task.apply_async(
            args=[biz.id,params.get("contact_email",""),params.get("message_hint","")],
            countdown=int(dh*3600))
        return {"type":"followup_scheduled","delay_hours":dh}

    if intent=="generate_page":
        prods=Product.query.filter_by(business_id=biz.id,active=True).all()
        biz.page_html=generate_business_page(biz,prods)
        biz.page_updated=datetime.utcnow(); db.session.commit()
        return {"type":"page_generated","url":f"/biz/{biz.slug}"}

    if intent=="connect_customer":
        room=LiveChatRoom.query.filter_by(business_id=biz.id,
            customer_email=params.get("contact_email","")).filter(
            LiveChatRoom.status!="closed").first()
        return {"type":"live_chat","room_id":room.room_id if room else None}

    return {}

# ── SERIALISERS ───────────────────────────────────────────────────
def s_biz(b): return {"id":b.id,"name":b.name,"slug":b.slug,"tagline":b.tagline,
    "description":b.description,"industry":b.industry,"website":b.website,
    "smtp_configured":bool(SMTP_USER),
    "page_url":f"/biz/{b.slug}" if b.page_html else None,
    "created_at":b.created_at.isoformat()}
def s_product(p): return {"id":p.id,"name":p.name,"description":p.description,"price":p.price,
    "currency":p.currency,"category":p.category,"image_url":p.image_url,"active":p.active}
def s_contact(c): return {"id":c.id,"name":c.name,"email":c.email,"company":c.company,
    "phone":c.phone,"status":c.status,"source":c.source,"notes":c.notes,
    "created_at":c.created_at.isoformat()}
def s_campaign(c): return {"id":c.id,"name":c.name,"subject":c.subject,"status":c.status,
    "sent_count":c.sent_count,"body_plain":c.body_plain,
    "scheduled_at":c.scheduled_at.isoformat() if c.scheduled_at else None,
    "sent_at":c.sent_at.isoformat() if c.sent_at else None,
    "contact_ids":json.loads(c.contact_ids or "[]"),
    "raw_emails":json.loads(c.raw_emails or "[]"),
    "created_at":c.created_at.isoformat()}
def s_thread(t): return {"id":t.id,"subject":t.subject,"from_email":t.from_email,
    "direction":t.direction,"intent":t.intent,"body_snippet":t.body_snippet,
    "ai_auto_reply":t.ai_auto_reply,"ai_reply_body":t.ai_reply_body,
    "received_at":t.received_at.isoformat()}

# ── AUTH ROUTES ───────────────────────────────────────────────────
@app.route("/api/auth/register",methods=["POST"])
def auth_register():
    d=request.get_json()
    if User.query.filter_by(email=d["email"]).first(): return jsonify({"error":"Email already registered"}),409
    u=User(email=d["email"],name=d.get("name","")); u.set_password(d["password"])
    db.session.add(u); db.session.commit()
    return jsonify({"token":create_access_token(identity=str(u.id)),
        "user":{"id":u.id,"email":u.email,"name":u.name,"business_id":u.business_id}})

@app.route("/api/auth/login",methods=["POST"])
def auth_login():
    d=request.get_json(); u=User.query.filter_by(email=d["email"]).first()
    if not u or not u.check_password(d.get("password","")): return jsonify({"error":"Invalid credentials"}),401
    return jsonify({"token":create_access_token(identity=str(u.id)),
        "refresh":create_refresh_token(identity=str(u.id)),
        "user":{"id":u.id,"email":u.email,"name":u.name,"business_id":u.business_id}})

@app.route("/api/auth/miracle/request",methods=["POST"])
def miracle_request():
    d=request.get_json(); u=User.query.filter_by(email=d.get("email","")).first()
    if u:
        token=str(uuid.uuid4()); u.miracle_token=token
        u.miracle_expiry=datetime.utcnow()+timedelta(hours=1); db.session.commit()
        body=f"Your MarketMe miracle login link:\n\n{APP_URL}/?miracle={token}\n\nExpires in 1 hour.\n\n— MarketMe"
        threading.Thread(target=smtp_send,
            args=(u.email,"✦ MarketMe — Your miracle login link",body),daemon=True).start()
    return jsonify({"message":"If that email is registered, a miracle link has been sent."})

@app.route("/api/auth/miracle/verify",methods=["POST"])
def miracle_verify():
    d=request.get_json(); u=User.query.filter_by(miracle_token=d.get("token","")).first()
    if not u or not u.miracle_expiry or u.miracle_expiry<datetime.utcnow():
        return jsonify({"error":"Invalid or expired miracle link"}),401
    u.miracle_token=None; u.miracle_expiry=None; db.session.commit()
    return jsonify({"token":create_access_token(identity=str(u.id)),
        "user":{"id":u.id,"email":u.email,"name":u.name,"business_id":u.business_id}})

# ── BUSINESS ──────────────────────────────────────────────────────
@app.route("/api/business",methods=["GET","POST"])
@jwt_required()
def route_business():
    uid=int(get_jwt_identity()); user=db.session.get(User,uid)
    if not user: return jsonify({"error":"Session expired, please log in again"}),401
    if request.method=="GET":
        if not user.business_id: return jsonify({"business":None})
    
        return jsonify({"business":s_biz(db.session.get(Business,user.business_id))})
    d=request.get_json()
    slug=re.sub(r"[^a-z0-9]+","-",d["name"].lower().strip()).strip("-")
    base,n=slug,1
    while Business.query.filter_by(slug=slug).first(): slug=f"{base}-{n}";n+=1
    biz=Business(name=d["name"],slug=slug,tagline=d.get("tagline",""),
        description=d.get("description",""),industry=d.get("industry",""))
    db.session.add(biz); db.session.flush(); user.business_id=biz.id; db.session.commit()
    # Seed from CSV on first business creation
    threading.Thread(target=_seed_contacts_from_csv,args=(biz.id,),daemon=True).start()
    return jsonify({"business":s_biz(biz)})

def _seed_contacts_from_csv(business_id):
    with app.app_context():
        for row in csv_load():
            if not Contact.query.filter_by(business_id=business_id,email=row["email"]).first():
                db.session.add(Contact(business_id=business_id,email=row["email"],
                    name=row.get("name",""),company=row.get("company",""),
                    phone=row.get("phone",""),notes=row.get("notes",""),source="csv"))
        db.session.commit()

@app.route("/api/business/settings",methods=["PUT"])
@jwt_required()
def business_settings():
    uid=int(get_jwt_identity()); user=db.session.get(User,uid)
    if not user: return jsonify({"error":"Session expired, please log in again"}),401
    biz=db.session.get(Business,user.business_id)
    if not biz: return jsonify({"error":"No business"}),404
    d=request.get_json()
    for f in ("name","tagline","description","industry","website"):
        if f in d and d[f] is not None: setattr(biz,f,d[f])
    db.session.commit(); return jsonify({"ok":True,"business":s_biz(biz)})

@app.route("/api/business/generate-page",methods=["POST"])
@jwt_required()
def gen_page():
    uid=int(get_jwt_identity()); user=db.session.get(User,uid)
    if not user: return jsonify({"error":"Session expired, please log in again"}),401
    biz=db.session.get(Business,user.business_id)
    if not biz: return jsonify({"error":"No business"}),404
    biz.page_html=generate_business_page(biz,
        Product.query.filter_by(business_id=biz.id,active=True).all())
    biz.page_updated=datetime.utcnow(); db.session.commit()
    return jsonify({"ok":True,"url":f"/biz/{biz.slug}"})

# ── PRODUCTS ──────────────────────────────────────────────────────
@app.route("/api/products",methods=["GET","POST"])
@jwt_required()
def route_products():
    uid=int(get_jwt_identity()); user=db.session.get(User,uid)
    if request.method=="GET":
        return jsonify({"products":[s_product(p) for p in
            Product.query.filter_by(business_id=user.business_id).all()]})
    d=request.get_json()
    p=Product(business_id=user.business_id,name=d["name"],description=d.get("description",""),
        price=float(d.get("price",0)),currency=d.get("currency","USD"),
        category=d.get("category",""),image_url=d.get("image_url",""))
    db.session.add(p); db.session.commit()
    return jsonify({"product":s_product(p)})

@app.route("/api/products/<int:pid>",methods=["DELETE"])
@jwt_required()
def delete_product(pid):
    p=Product.query.get_or_404(pid); db.session.delete(p); db.session.commit()
    return jsonify({"ok":True})

# ── CONTACTS ──────────────────────────────────────────────────────
@app.route("/api/contacts",methods=["GET","POST"])
@jwt_required()
def route_contacts():
    uid=int(get_jwt_identity()); user=db.session.get(User,uid)
    if not user: return jsonify({"error":"Session expired, please log in again"}),401
    if request.method=="GET":
        return jsonify({"contacts":[s_contact(c) for c in
            Contact.query.filter_by(business_id=user.business_id)
                         .order_by(Contact.created_at.desc()).all()]})
    d=request.get_json()
    c=Contact(business_id=user.business_id,email=d["email"],name=d.get("name",""),
        company=d.get("company",""),phone=d.get("phone",""),
        notes=d.get("notes",""),source="manual")
    db.session.add(c); db.session.commit()
    csv_append(d.get("name",""),d["email"],d.get("company",""),d.get("phone",""),d.get("notes",""))
    return jsonify({"contact":s_contact(c)})

@app.route("/api/contacts/<int:cid>",methods=["DELETE"])
@jwt_required()
def delete_contact(cid):
    c=Contact.query.get_or_404(cid); db.session.delete(c); db.session.commit()
    return jsonify({"ok":True})

@app.route("/api/contacts/import",methods=["POST"])
@jwt_required()
def import_contacts():
    """Import contacts from CSV file upload or JSON list of emails"""
    uid=int(get_jwt_identity()); user=db.session.get(User,uid)
    if not user: return jsonify({"error":"Session expired, please log in again"}),401
    added=0

    # CSV file upload
    if "file" in request.files:
        f=request.files["file"]
        content=f.read().decode("utf-8",errors="replace")
        reader=csv.DictReader(io.StringIO(content))
        for row in reader:
            email=(row.get("email") or row.get("Email") or "").strip()
            if not email or "@" not in email: continue
            name=(row.get("name") or row.get("Name") or "").strip()
            company=(row.get("company") or row.get("Company") or "").strip()
            if not Contact.query.filter_by(business_id=user.business_id,email=email).first():
                db.session.add(Contact(business_id=user.business_id,email=email,
                    name=name,company=company,source="csv"))
                csv_append(name,email,company)
                added+=1
        db.session.commit()
        return jsonify({"ok":True,"added":added})

    # JSON list of raw emails
    d=request.get_json()
    raw=d.get("emails",[])
    for item in raw:
        email=(item.get("email",item) if isinstance(item,dict) else item).strip()
        if not email or "@" not in email: continue
        name=(item.get("name","") if isinstance(item,dict) else "").strip()
        if not Contact.query.filter_by(business_id=user.business_id,email=email).first():
            db.session.add(Contact(business_id=user.business_id,email=email,
                name=name,source="import"))
            csv_append(name,email)
            added+=1
    db.session.commit()
    return jsonify({"ok":True,"added":added})

@app.route("/api/contacts/csv-pool",methods=["GET"])
@jwt_required()
def csv_pool():
    """Return shared CSV contacts pool"""
    return jsonify({"contacts":csv_load()})

# ── CAMPAIGNS ─────────────────────────────────────────────────────
@app.route("/api/campaigns",methods=["GET","POST"])
@jwt_required()
def route_campaigns():
    uid=int(get_jwt_identity()); user=db.session.get(User,uid)
    if not user: return jsonify({"error":"Session expired, please log in again"}),401
    if request.method=="GET":
        return jsonify({"campaigns":[s_campaign(c) for c in
            Campaign.query.filter_by(business_id=user.business_id)
                          .order_by(Campaign.created_at.desc()).all()]})
    d=request.get_json()
    sched=datetime.fromisoformat(d["scheduled_at"]) if d.get("scheduled_at") else None
    cp=Campaign(business_id=user.business_id,name=d.get("name",""),
        subject=d.get("subject",""),body_html=d.get("body_html",""),
        body_plain=d.get("body_plain",""),
        contact_ids=json.dumps(d.get("contact_ids",[])),
        raw_emails=json.dumps(d.get("raw_emails",[])),
        status="scheduled" if(d.get("send_now") or sched) else "draft",
        scheduled_at=sched or(datetime.utcnow() if d.get("send_now") else None))
    db.session.add(cp); db.session.commit()
    return jsonify({"campaign":s_campaign(cp)})

@app.route("/api/campaigns/<int:cid>/send",methods=["POST"])
@jwt_required()
def send_campaign_now(cid):
    cp=Campaign.query.get_or_404(cid)
    cp.status="scheduled"; cp.scheduled_at=datetime.utcnow(); db.session.commit()
    return jsonify({"ok":True})

@app.route("/api/email-threads")
@jwt_required()
def route_threads():
    uid=int(get_jwt_identity()); user=db.session.get(User,uid)
    if not user: return jsonify({"error":"Session expired, please log in again"}),401
    return jsonify({"threads":[s_thread(t) for t in
        EmailThread.query.filter_by(business_id=user.business_id)
                         .order_by(EmailThread.received_at.desc()).limit(100).all()]})

# ── CHAT (text + image) ───────────────────────────────────────────
@app.route("/api/chat",methods=["POST"])
@jwt_required()
def route_chat():
    uid=int(get_jwt_identity()); user=db.session.get(User,uid)
    if not user: return jsonify({"error":"Session expired, please log in again"}),401
    biz=db.session.get(Business,user.business_id) if user.business_id else None
    d=request.get_json()
    session_id=d.get("session_id",str(uuid.uuid4()))
    messages=d.get("messages",[])
    image_b64=d.get("image_b64")   # optional base64 image
    image_mime=d.get("image_mime","image/jpeg")

    db.session.add(ChatLog(business_id=user.business_id,session_id=session_id,
        role="user",content=messages[-1]["content"] if messages else ""))
    db.session.commit()

    result=chat_with_nova(messages,biz,image_b64,image_mime)

    action={}
    if result["intent"]:
        if result["intent"] in ("navigate","open_modal","toggle_theme","show_notification"):
            action={"type":result["intent"],**result["params"]}
        elif biz:
            action=handle_intent(result["intent"],result["params"],user,biz)

    db.session.add(ChatLog(business_id=user.business_id,session_id=session_id,
        role="assistant",content=result["content"],intent_detected=result["intent"]))
    db.session.commit()
    return jsonify({"content":result["content"],"intent":result["intent"],
                    "params":result["params"],"action":action})

# ── PUBLIC BIZ PAGE ───────────────────────────────────────────────
@app.route("/biz/<slug>")
def biz_page(slug):
    biz=Business.query.filter_by(slug=slug).first_or_404()
    if biz.page_html:
        widget=f"""<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
<script>(function(){{
var btn=document.createElement('button');btn.innerHTML='💬 Chat with us';
btn.style='position:fixed;bottom:24px;right:24px;background:#f59e0b;color:#000;font-weight:700;padding:12px 24px;border:none;border-radius:8px;cursor:pointer;z-index:9999;box-shadow:0 4px 20px rgba(245,158,11,.4)';
btn.onclick=function(){{window.openLiveChat&&window.openLiveChat();}};document.body.appendChild(btn);
var _s=null;window.openLiveChat=function(){{
var name=prompt('Your name:','');var email=prompt('Your email:','');if(!name||!email)return;
_s=io('{APP_URL}');_s.emit('customer_join',{{slug:'{slug}',name:name,email:email}});
_s.on('chat_ready',function(d){{
var div=document.createElement('div');div.id='mm-chat';
div.style='position:fixed;bottom:90px;right:24px;width:340px;height:480px;background:#0f172a;border-radius:16px;border:1px solid #1e293b;display:flex;flex-direction:column;z-index:9999;box-shadow:0 20px 60px rgba(0,0,0,.5)';
div.innerHTML='<div style="padding:16px;border-bottom:1px solid #1e293b;color:#f1f5f9;font-weight:600;display:flex;justify-content:space-between"><span>Chat · {biz.name}</span><span onclick="document.getElementById(\\\'mm-chat\\\').remove()" style="cursor:pointer;color:#64748b">✕</span></div><div id="mm-msgs" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px"></div><div style="padding:12px;border-top:1px solid #1e293b;display:flex;gap:8px"><input id="mm-inp" placeholder="Type a message..." style="flex:1;background:#1e293b;border:none;border-radius:6px;padding:8px 12px;color:#f1f5f9;font-size:14px;outline:none" onkeydown="if(event.key===\'Enter\')mmSend()"><button onclick="mmSend()" style="background:#f59e0b;border:none;border-radius:6px;padding:8px 14px;font-weight:600;cursor:pointer">Send</button></div>';
document.body.appendChild(div);var rid=d.room_id;
_s.on('live_message',function(m){{var el=document.getElementById('mm-msgs');var mine=m.sender===name;
el.innerHTML+='<div style="background:'+(mine?'#f59e0b22':'#1e293b')+';padding:8px 12px;border-radius:8px;font-size:13px;color:'+(mine?'#fbbf24':'#cbd5e1')+';text-align:'+(mine?'right':'left')+'"><b>'+m.sender+':</b> '+m.text+'</div>';
el.scrollTop=el.scrollHeight;}});
window.mmSend=function(){{var inp=document.getElementById('mm-inp');if(!inp.value.trim())return;
_s.emit('live_message',{{room_id:rid,sender:name,text:inp.value}});
var el=document.getElementById('mm-msgs');el.innerHTML+='<div style="background:#f59e0b22;padding:8px 12px;border-radius:8px;font-size:13px;color:#fbbf24;text-align:right"><b>You:</b> '+inp.value+'</div>';
el.scrollTop=el.scrollHeight;inp.value='';}};}}); }};
}})();</script>"""
        return biz.page_html.replace("</body>",widget+"</body>")
    return render_template_string(
        "<!DOCTYPE html><html><head><title>{{b.name}}</title>"
        "<script src='https://cdn.tailwindcss.com'></script></head>"
        "<body class='bg-slate-900 text-slate-100 min-h-screen flex items-center justify-center'>"
        "<div class='text-center'><h1 class='text-5xl font-bold text-amber-400 mb-4'>{{b.name}}</h1>"
        "<p class='text-slate-400 text-xl'>{{b.tagline or 'Coming soon...'}}</p></div></body></html>",b=biz)

@app.route("/")
@app.route("/dashboard")
@app.route("/miracle")
def serve_spa(): return render_template_string(SPA_HTML)

# ── SOCKETIO ──────────────────────────────────────────────────────
@socketio.on("join_biz")
def sio_join_biz(data):
    bid=data.get("business_id")
    if bid: join_room(f"biz_{bid}"); emit("joined",{"room":f"biz_{bid}"})

@socketio.on("customer_join")
def sio_customer_join(data):
    biz=Business.query.filter_by(slug=data.get("slug","")).first()
    if not biz: return
    room_id=str(uuid.uuid4())[:8]
    room=LiveChatRoom(business_id=biz.id,room_id=room_id,
        customer_name=data.get("name","Guest"),customer_email=data.get("email",""))
    db.session.add(room); db.session.commit(); join_room(room_id)
    emit("new_chat_request",{"room_id":room_id,"customer_name":room.customer_name,
                              "customer_email":room.customer_email},room=f"biz_{biz.id}")
    emit("chat_ready",{"room_id":room_id})
    csv_append(data.get("name",""),data.get("email",""),notes="Live chat visitor")

@socketio.on("owner_join_chat")
def sio_owner_join(data):
    room_id=data.get("room_id"); join_room(room_id)
    room=LiveChatRoom.query.filter_by(room_id=room_id).first()
    if room: room.status="active"; db.session.commit()
    emit("owner_joined",{"room_id":room_id},room=room_id)

@socketio.on("live_message")
def sio_live_msg(data):
    emit("live_message",{"sender":data.get("sender","Unknown"),
        "text":data.get("text",""),"ts":datetime.utcnow().isoformat()},
        room=data.get("room_id"),include_self=False)

_voice_sessions:dict={}

@socketio.on("voice_start")
def sio_voice_start(data):
    sid=request.sid
    if sid in _voice_sessions: _voice_sessions[sid]["active"]=False
    sess={"active":True,"queue":[],"api_key":NOVA_API_KEY}
    _voice_sessions[sid]=sess
    threading.Thread(target=_voice_thread,args=(sid,sess),daemon=True).start()
    emit("voice_ready")

@socketio.on("voice_audio")
def sio_voice_audio(data):
    sid=request.sid
    if sid in _voice_sessions: _voice_sessions[sid]["queue"].append(data.get("audio",""))

@socketio.on("voice_stop")
def sio_voice_stop():
    sid=request.sid
    if sid in _voice_sessions: _voice_sessions[sid]["active"]=False; del _voice_sessions[sid]

@socketio.on("disconnect")
def sio_disconnect():
    sid=request.sid
    if sid in _voice_sessions: _voice_sessions[sid]["active"]=False; del _voice_sessions[sid]

def _voice_thread(sid,sess):
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try: loop.run_until_complete(_nova_sonic_bridge(sid,sess))
    except Exception as e: socketio.emit("voice_error",{"error":str(e)},room=sid)
    finally: loop.close()

async def _nova_sonic_bridge(sid,sess):
    ssl_ctx=ssl.create_default_context(); ssl_ctx.check_hostname=False; ssl_ctx.verify_mode=ssl.CERT_NONE
    hdrs={"Authorization":f"Bearer {sess['api_key']}","Origin":"https://api.nova.amazon.com"}
    try:
        async with ws_lib.connect(NOVA_WS_URL,ssl=ssl_ctx,additional_headers=hdrs) as ws:
            ev=json.loads(await ws.recv())
            if ev.get("type")!="session.created": return
            await ws.send(json.dumps({"type":"session.update","session":{"type":"realtime",
                "instructions":"You are MarketMe Voice Agent — a smart, concise AI marketing assistant. Help with campaigns, leads, products and business strategy.",
                "audio":{"input":{"turn_detection":{"threshold":0.5}},"output":{"voice":"matthew"}}}}))
            await ws.recv(); socketio.emit("voice_session_active",{},room=sid)
            async def _send():
                while sess["active"]:
                    if sess["queue"]: await ws.send(json.dumps({"type":"input_audio_buffer.append","audio":sess["queue"].pop(0)}))
                    else: await asyncio.sleep(0.04)
            async def _recv():
                while sess["active"]:
                    try:
                        ev=json.loads(await asyncio.wait_for(ws.recv(),timeout=1.0)); t=ev.get("type","")
                        if t=="response.output_audio.delta": socketio.emit("voice_audio_out",{"audio":ev["delta"]},room=sid)
                        elif t=="response.output_audio_transcript.done": socketio.emit("voice_transcript",{"text":ev.get("transcript","")},room=sid)
                        elif t=="error": socketio.emit("voice_error",{"error":ev.get("error",{})},room=sid)
                    except asyncio.TimeoutError: pass
                    except Exception: break
            await asyncio.gather(_send(),_recv())
    except Exception as e: socketio.emit("voice_error",{"error":str(e)},room=sid)


# ═══════════════════════════════════════════════════════════════════
# SPA HTML
# ═══════════════════════════════════════════════════════════════════
SPA_HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MarketMe — AI Marketing Agent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
<style>
/* ── Theme variables ── */
[data-theme="dark"]{
  --bg:#070711;--surface:#0e0e1c;--card:#13131f;--border:#1d1d2e;
  --accent:#f59e0b;--accent2:#fbbf24;--green:#10b981;--red:#f43f5e;
  --text:#e2e8f0;--muted:#64748b;
}
[data-theme="light"]{
  --bg:#f8fafc;--surface:#ffffff;--card:#f1f5f9;--border:#e2e8f0;
  --accent:#d97706;--accent2:#f59e0b;--green:#059669;--red:#e11d48;
  --text:#0f172a;--muted:#64748b;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Syne',sans-serif;min-height:100vh;transition:background .3s,color .3s}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:var(--surface)}::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;background:radial-gradient(ellipse at 50% 0%,#f59e0b18 0%,transparent 60%)}
.auth-card{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:40px;width:420px;max-width:95vw}
.auth-logo{font-size:28px;font-weight:800;letter-spacing:-1px;color:var(--accent);margin-bottom:8px}
.auth-sub{color:var(--muted);font-size:14px;margin-bottom:32px;font-family:'DM Mono',monospace}
.auth-tabs{display:flex;gap:4px;background:var(--card);border-radius:8px;padding:4px;margin-bottom:28px}
.auth-tab{flex:1;padding:8px;text-align:center;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;color:var(--muted);transition:.2s}
.auth-tab.active{background:var(--accent);color:#000}
.inp{width:100%;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:11px 14px;color:var(--text);font-family:'DM Mono',monospace;font-size:14px;outline:none;transition:.2s}
.inp:focus{border-color:var(--accent)}
.inp-group{margin-bottom:14px}.inp-label{font-size:12px;color:var(--muted);margin-bottom:6px;font-family:'DM Mono',monospace;display:block}
.btn-primary{width:100%;padding:12px;background:var(--accent);color:#000;font-weight:700;border:none;border-radius:8px;cursor:pointer;font-family:'Syne',sans-serif;font-size:15px;transition:.2s}
.btn-primary:hover{background:var(--accent2)}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--muted);padding:8px 16px;border-radius:8px;cursor:pointer;font-family:'Syne',sans-serif;font-size:13px;transition:.2s}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}
.err-msg{color:var(--red);font-size:13px;font-family:'DM Mono',monospace;margin-top:8px;min-height:20px}
#app{display:flex;height:100vh;overflow:hidden}
.sidebar{width:220px;min-width:220px;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;padding:24px 0;transition:background .3s}
.logo{padding:0 20px 24px;font-size:22px;font-weight:800;color:var(--accent);letter-spacing:-1px;border-bottom:1px solid var(--border)}
.logo span{color:var(--text)}
.nav-section{padding:16px 12px 8px;font-size:10px;font-family:'DM Mono',monospace;color:var(--muted);letter-spacing:.1em;text-transform:uppercase}
.nav-item{display:flex;align-items:center;gap:10px;padding:9px 20px;cursor:pointer;color:var(--muted);font-size:14px;font-weight:600;transition:.15s;border-left:2px solid transparent}
.nav-item:hover{color:var(--text);background:var(--card)}.nav-item.active{color:var(--accent);border-left-color:var(--accent);background:var(--card)}
.nav-icon{width:16px;text-align:center;font-size:15px}
.sidebar-footer{margin-top:auto;padding:16px 20px;border-top:1px solid var(--border)}
.biz-badge{font-size:12px;color:var(--muted);font-family:'DM Mono',monospace}
.biz-name{font-size:14px;font-weight:700;color:var(--text);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.main{flex:1;overflow-y:auto;background:var(--bg)}
.panel{display:none;height:100%;flex-direction:column}.panel.active{display:flex}
.panel-header{padding:28px 32px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.panel-title{font-size:22px;font-weight:800;letter-spacing:-.5px}
.panel-subtitle{font-size:13px;color:var(--muted);font-family:'DM Mono',monospace;margin-top:2px}
.panel-body{flex:1;overflow-y:auto;padding:24px 32px}
/* Chat */
.chat-area{flex:1;overflow-y:auto;padding:24px 32px;display:flex;flex-direction:column;gap:16px}
.msg{display:flex;flex-direction:column;max-width:75%}
.msg.user{align-self:flex-end;align-items:flex-end}.msg.assistant{align-self:flex-start;align-items:flex-start}
.msg-bubble{padding:12px 16px;border-radius:12px;font-size:14px;line-height:1.6;white-space:pre-wrap;word-break:break-word}
.msg.user .msg-bubble{background:#f59e0b1a;border:1px solid #f59e0b44;color:var(--text);border-bottom-right-radius:4px}
.msg.assistant .msg-bubble{background:var(--surface);border:1px solid var(--border);color:var(--text);border-bottom-left-radius:4px}
.msg-img{max-width:240px;border-radius:8px;margin-bottom:6px;border:1px solid var(--border)}
.msg-time{font-size:11px;color:var(--muted);font-family:'DM Mono',monospace;margin-top:4px}
.intent-badge{display:inline-flex;align-items:center;gap:5px;background:var(--card);border:1px solid var(--accent);color:var(--accent);border-radius:6px;padding:3px 10px;font-size:11px;font-family:'DM Mono',monospace;margin-top:8px}
.action-card{background:var(--card);border:1px solid var(--green);border-radius:8px;padding:10px 14px;margin-top:8px;font-size:12px;font-family:'DM Mono',monospace;color:var(--green)}
.chat-input-wrap{padding:16px 32px 20px;border-top:1px solid var(--border);flex-shrink:0}
.chat-input-row{display:flex;gap:8px;align-items:center}
.chat-inp-field{flex:1;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:11px 16px;color:var(--text);font-family:'Syne',sans-serif;font-size:14px;outline:none;transition:.2s}
.chat-inp-field:focus{border-color:var(--accent)}
.chat-send-btn{background:var(--accent);border:none;border-radius:10px;width:44px;height:44px;color:#000;font-size:18px;cursor:pointer;flex-shrink:0;transition:.2s}
.chat-send-btn:hover{background:var(--accent2)}
.icon-btn{background:var(--surface);border:1px solid var(--border);border-radius:10px;width:44px;height:44px;color:var(--muted);font-size:18px;cursor:pointer;flex-shrink:0;transition:.2s;display:flex;align-items:center;justify-content:center}
.icon-btn:hover{border-color:var(--accent);color:var(--accent)}
.icon-btn.active{background:#f43f5e22;border-color:var(--red);color:var(--red);animation:pulse 1.5s infinite}
.img-preview{width:44px;height:44px;border-radius:8px;object-fit:cover;border:2px solid var(--accent);cursor:pointer}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 #f43f5e44}50%{box-shadow:0 0 0 8px transparent}}
.chat-hint{font-size:11px;color:var(--muted);font-family:'DM Mono',monospace;margin-bottom:8px}
.typing-dot{display:inline-flex;gap:4px;padding:4px 0}
.typing-dot span{width:6px;height:6px;background:var(--muted);border-radius:50%;animation:bounce .8s infinite}
.typing-dot span:nth-child(2){animation-delay:.15s}.typing-dot span:nth-child(3){animation-delay:.3s}
@keyframes bounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}
/* Voice */
.voice-center{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:24px}
.mic-btn{width:96px;height:96px;border-radius:50%;background:var(--surface);border:2px solid var(--border);font-size:36px;cursor:pointer;transition:.2s;display:flex;align-items:center;justify-content:center}
.mic-btn.recording{background:#f43f5e22;border-color:var(--red);animation:pulse 1.5s infinite}
.voice-status{font-family:'DM Mono',monospace;font-size:14px;color:var(--muted)}
.transcript-box{max-width:600px;width:100%;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;min-height:120px;max-height:300px;overflow-y:auto;font-size:14px;line-height:1.7;color:var(--text);font-family:'DM Mono',monospace;white-space:pre-wrap}
/* Cards/Tables */
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px}
.card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}
.product-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px}
.product-name{font-size:15px;font-weight:700;margin-bottom:4px}
.product-price{font-size:22px;font-weight:800;color:var(--accent);font-family:'DM Mono',monospace}
.product-desc{font-size:13px;color:var(--muted);margin-top:6px}
.product-category{font-size:11px;background:var(--border);color:var(--muted);border-radius:4px;padding:2px 8px;font-family:'DM Mono',monospace;display:inline-block;margin-top:8px}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:10px 14px;font-size:11px;font-family:'DM Mono',monospace;color:var(--muted);border-bottom:1px solid var(--border);letter-spacing:.06em;text-transform:uppercase;font-weight:500}
td{padding:12px 14px;font-size:13px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:none}tr:hover td{background:var(--card)}
.status-badge{display:inline-flex;align-items:center;padding:3px 10px;border-radius:20px;font-size:11px;font-family:'DM Mono',monospace;font-weight:500}
.status-new{background:#64748b22;color:#94a3b8}.status-contacted{background:#38bdf822;color:#38bdf8}
.status-agreed{background:#10b98122;color:#10b981}.status-declined{background:#f43f5e22;color:#f43f5e}
.status-interested{background:#f59e0b22;color:#f59e0b}.status-csv{background:#8b5cf622;color:#8b5cf6}
.status-import{background:#06b6d422;color:#06b6d4}
.status-unresponsive,.status-draft{background:#64748b22;color:#94a3b8}
.status-scheduled{background:#38bdf822;color:#38bdf8}.status-sending{background:#f59e0b22;color:#f59e0b}
.status-sent{background:#10b98122;color:#10b981}
.intent-agreed{background:#10b98122;color:#10b981}.intent-declined{background:#f43f5e22;color:#f43f5e}
.intent-interested{background:#f59e0b22;color:#f59e0b}.intent-question{background:#38bdf822;color:#38bdf8}
.intent-other{background:#64748b22;color:#64748b}
/* Modals */
.modal-overlay{position:fixed;inset:0;background:#00000099;display:flex;align-items:center;justify-content:center;z-index:100}
.modal-box{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:32px;width:560px;max-width:94vw;max-height:90vh;overflow-y:auto}
.modal-title{font-size:18px;font-weight:800;margin-bottom:20px}
.form-row{margin-bottom:14px}.form-label{font-size:12px;color:var(--muted);font-family:'DM Mono',monospace;margin-bottom:5px;display:block}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.btn-sm{padding:6px 14px;border:none;border-radius:6px;cursor:pointer;font-family:'Syne',sans-serif;font-size:12px;font-weight:600;transition:.2s}
.btn-accent{background:var(--accent);color:#000}.btn-accent:hover{background:var(--accent2)}
.btn-danger{background:#f43f5e22;color:var(--red);border:1px solid #f43f5e44}
.btn-outline{background:transparent;border:1px solid var(--border);color:var(--muted)}
.btn-outline:hover{border-color:var(--accent);color:var(--accent)}
.btn-purple{background:#8b5cf622;color:#8b5cf6;border:1px solid #8b5cf644}
.notification{position:fixed;bottom:24px;right:24px;background:var(--surface);border:1px solid var(--accent);border-radius:10px;padding:12px 18px;font-size:13px;z-index:200;max-width:320px;font-family:'DM Mono',monospace;box-shadow:0 8px 32px rgba(0,0,0,.4);transition:opacity .3s}
.notify-title{color:var(--accent);font-weight:600;margin-bottom:2px}.notify-body{color:var(--muted)}
.onboard-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;background:radial-gradient(ellipse at 50% 0%,#f59e0b18 0%,transparent 60%)}
.onboard-card{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:40px;width:520px;max-width:95vw}
.step-dot{width:8px;height:8px;border-radius:50%;background:var(--border);transition:.3s}.step-dot.active{background:var(--accent)}
select.inp option{background:var(--card)}
.room-item{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px;cursor:pointer;transition:.15s;margin-bottom:8px}
.room-item:hover,.room-item.active-room{border-color:var(--accent)}
.theme-toggle{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:4px 12px;font-size:12px;cursor:pointer;color:var(--muted);font-family:'DM Mono',monospace;transition:.2s}
.theme-toggle:hover{border-color:var(--accent);color:var(--accent)}
/* Email chip input */
.email-chips{display:flex;flex-wrap:wrap;gap:6px;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:8px;min-height:44px;cursor:text}
.email-chips:focus-within{border-color:var(--accent)}
.email-chip{background:var(--accent);color:#000;border-radius:20px;padding:2px 10px;font-size:12px;font-family:'DM Mono',monospace;display:flex;align-items:center;gap:4px}
.chip-x{cursor:pointer;font-weight:700;opacity:.7}.chip-x:hover{opacity:1}
.email-chips input{border:none;outline:none;background:transparent;color:var(--text);font-size:13px;font-family:'DM Mono',monospace;flex:1;min-width:160px}
.drop-zone{border:2px dashed var(--border);border-radius:8px;padding:20px;text-align:center;cursor:pointer;transition:.2s;color:var(--muted);font-size:13px;font-family:'DM Mono',monospace}
.drop-zone:hover,.drop-zone.dragover{border-color:var(--accent);color:var(--accent);background:#f59e0b08}
</style>
</head>
<body>

<!-- AUTH -->
<div id="auth" class="auth-wrap">
  <div class="auth-card">
    <div class="auth-logo">Market<span>Me</span></div>
    <div class="auth-sub">AI Marketing Agent Platform</div>
    <div class="auth-tabs">
      <div class="auth-tab active" onclick="authTab('login')">Login</div>
      <div class="auth-tab" onclick="authTab('register')">Register</div>
      <div class="auth-tab" onclick="authTab('miracle')">Miracle Link</div>
    </div>
    <div id="form-login">
      <div class="inp-group"><label class="inp-label">Email</label><input class="inp" id="l-email" type="email" placeholder="you@company.com"></div>
      <div class="inp-group"><label class="inp-label">Password</label><input class="inp" id="l-pass" type="password" placeholder="••••••••" onkeydown="if(event.key==='Enter')doLogin()"></div>
      <div class="err-msg" id="l-err"></div>
      <button class="btn-primary" style="margin-top:8px" onclick="doLogin()">Sign In</button>
    </div>
    <div id="form-register" style="display:none">
      <div class="inp-group"><label class="inp-label">Full Name</label><input class="inp" id="r-name" type="text" placeholder="Jane Smith"></div>
      <div class="inp-group"><label class="inp-label">Email</label><input class="inp" id="r-email" type="email" placeholder="you@company.com"></div>
      <div class="inp-group"><label class="inp-label">Password</label><input class="inp" id="r-pass" type="password" placeholder="••••••••"></div>
      <div class="err-msg" id="r-err"></div>
      <button class="btn-primary" style="margin-top:8px" onclick="doRegister()">Create Account</button>
    </div>
    <div id="form-miracle" style="display:none">
      <div class="inp-group"><label class="inp-label">Email Address</label><input class="inp" id="m-email" type="email" placeholder="you@company.com"></div>
      <div id="m-msg" style="color:var(--green);font-size:13px;font-family:'DM Mono',monospace;min-height:20px;margin-top:8px"></div>
      <button class="btn-primary" style="margin-top:8px" onclick="doMiracle()">Send Miracle Link</button>
    </div>
  </div>
</div>

<!-- ONBOARDING -->
<div id="onboard" class="onboard-wrap" style="display:none">
  <div class="onboard-card">
    <div style="display:flex;gap:8px;margin-bottom:28px">
      <div class="step-dot active" id="step1-dot"></div>
      <div class="step-dot" id="step2-dot"></div>
    </div>
    <div id="ob-step1">
      <div class="auth-logo" style="font-size:22px;margin-bottom:6px">Set up your business</div>
      <div class="auth-sub" style="margin-bottom:24px">Tell MarketMe about your business</div>
      <div class="form-grid">
        <div class="form-row"><label class="form-label">Business Name *</label><input class="inp" id="ob-name" placeholder="Acme Inc."></div>
        <div class="form-row"><label class="form-label">Industry</label>
          <select class="inp" id="ob-industry"><option value="">Select...</option>
            <option>E-commerce</option><option>SaaS / Software</option><option>Consulting</option>
            <option>Real Estate</option><option>Healthcare</option><option>Education</option>
            <option>Food &amp; soft drinks</option><option>Finance</option><option>Marketing Agency</option><option>Other</option>
          </select>
        </div>
      </div>
      <div class="form-row"><label class="form-label">Tagline</label><input class="inp" id="ob-tagline" placeholder="Your one-liner pitch"></div>
      <div class="form-row"><label class="form-label">Description</label><textarea class="inp" id="ob-desc" rows="3" placeholder="What does your business do?"></textarea></div>
      <div class="err-msg" id="ob-err"></div>
      <button class="btn-primary" style="margin-top:16px" onclick="obFinish()">Launch MarketMe →</button>
    </div>
  </div>
</div>

<!-- APP -->
<div id="app" style="display:none">
  <div class="sidebar">
    <div class="logo">Market<span>Me</span></div>
    <div class="nav-section">Agent</div>
    <div class="nav-item active" data-panel="chat" onclick="showPanel('chat')"><span class="nav-icon">💬</span>Chat</div>
    <div class="nav-item" data-panel="voice" onclick="showPanel('voice')"><span class="nav-icon">🎙️</span>Voice</div>
    <div class="nav-section">Business</div>
    <div class="nav-item" data-panel="products" onclick="showPanel('products')"><span class="nav-icon">📦</span>Products</div>
    <div class="nav-item" data-panel="contacts" onclick="showPanel('contacts')"><span class="nav-icon">👥</span>Contacts</div>
    <div class="nav-item" data-panel="campaigns" onclick="showPanel('campaigns')"><span class="nav-icon">📣</span>Campaigns</div>
    <div class="nav-section">Monitor</div>
    <div class="nav-item" data-panel="inbox" onclick="showPanel('inbox')">
      <span class="nav-icon">📥</span>Inbox
      <span id="inbox-dot" style="display:none;background:var(--red);width:7px;height:7px;border-radius:50%;margin-left:auto;flex-shrink:0"></span>
    </div>
    <div class="nav-item" data-panel="livechats" onclick="showPanel('livechats')"><span class="nav-icon">⚡</span>Live Chats</div>
    <div class="nav-section">Setup</div>
    <div class="nav-item" data-panel="settings" onclick="showPanel('settings')"><span class="nav-icon">⚙️</span>Settings</div>
    <div class="sidebar-footer">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div class="biz-badge">BUSINESS</div>
        <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙 Dark</button>
      </div>
      <div class="biz-name" id="sb-biz-name">—</div>
      <div style="margin-top:8px"><button class="btn-ghost btn-sm" onclick="logout()">Sign out</button></div>
    </div>
  </div>

  <div class="main">
    <!-- CHAT -->
    <div id="panel-chat" class="panel active" style="height:100%;flex-direction:column">
      <div class="panel-header" style="flex-shrink:0">
        <div><div class="panel-title">MarketMe Agent</div>
        <div class="panel-subtitle">Nova 2 Lite · web grounding · image analysis · UI control</div></div>
        <button class="btn-ghost btn-sm" onclick="clearChat()">Clear chat</button>
      </div>
      <div class="chat-area" id="chat-area"></div>
      <div class="chat-input-wrap">
        <div class="chat-hint">Try: "Show me products" · "Go to contacts" · "Find leads in Lagos" · "Analyse this image" · "Switch to dark mode"</div>
        <div style="margin-bottom:8px" id="img-preview-wrap" style="display:none"></div>
        <div class="chat-input-row">
          <label class="icon-btn" title="Upload image" style="cursor:pointer">
            📎<input type="file" id="img-upload" accept="image/*" style="display:none" onchange="handleImgUpload(this)">
          </label>
          <input class="chat-inp-field" id="chat-inp" type="text" placeholder="Message MarketMe Agent..." onkeydown="if(event.key==='Enter')sendChat()">
          <button class="icon-btn" id="voice-toggle-btn" onclick="toggleVoiceInChat()" title="Voice mode">🎙️</button>
          <button class="chat-send-btn" onclick="sendChat()">↑</button>
        </div>
      </div>
    </div>

    <!-- VOICE -->
    <div id="panel-voice" class="panel" style="height:100%">
      <div class="panel-header"><div><div class="panel-title">Voice Agent</div><div class="panel-subtitle">Nova 2 Sonic · Real-time bidirectional speech · 8 min sessions</div></div></div>
      <div class="voice-center">
        <div style="text-align:center">
          <button class="mic-btn" id="mic-btn" onclick="toggleVoice()">🎙️</button>
          <div class="voice-status" id="voice-status" style="margin-top:16px">Click to start voice session</div>
        </div>
        <div style="width:100%;max-width:600px">
          <div style="font-size:12px;font-family:'DM Mono',monospace;color:var(--muted);margin-bottom:8px;text-transform:uppercase;letter-spacing:.06em">Transcript</div>
          <div class="transcript-box" id="transcript-box">Your conversation will appear here...</div>
        </div>
      </div>
    </div>

    <!-- PRODUCTS -->
    <div id="panel-products" class="panel">
      <div class="panel-header">
        <div><div class="panel-title">Products &amp; Services</div><div class="panel-subtitle">Shown on your AI-generated business page</div></div>
        <button class="btn-sm btn-accent" onclick="showModal('modal-add-product')">+ Add Product</button>
      </div>
      <div class="panel-body">
        <div class="card-grid" id="products-grid"><div style="color:var(--muted);font-family:'DM Mono',monospace;font-size:13px">Loading...</div></div>
      </div>
    </div>

    <!-- CONTACTS -->
    <div id="panel-contacts" class="panel">
      <div class="panel-header">
        <div><div class="panel-title">Contacts</div><div class="panel-subtitle" id="contacts-count">—</div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn-sm btn-purple" onclick="showModal('modal-import-contacts')">📥 Import</button>
          <button class="btn-sm btn-outline" onclick="showModal('modal-find-leads')">🔍 Find Leads</button>
          <button class="btn-sm btn-accent" onclick="showModal('modal-add-contact')">+ Add</button>
        </div>
      </div>
      <div class="panel-body" style="padding-top:0">
        <div class="card" style="overflow:hidden">
          <table><thead><tr><th>Name</th><th>Email</th><th>Company</th><th>Status</th><th>Source</th><th></th></tr></thead>
          <tbody id="contacts-table"></tbody></table>
        </div>
      </div>
    </div>

    <!-- CAMPAIGNS -->
    <div id="panel-campaigns" class="panel">
      <div class="panel-header">
        <div><div class="panel-title">Campaigns</div><div class="panel-subtitle">Celery sends in background even when app is closed</div></div>
        <button class="btn-sm btn-accent" onclick="showModal('modal-add-campaign')">+ New Campaign</button>
      </div>
      <div class="panel-body" style="padding-top:0">
        <div class="card" style="overflow:hidden">
          <table><thead><tr><th>Campaign</th><th>Subject</th><th>Status</th><th>Sent</th><th>Scheduled</th><th></th></tr></thead>
          <tbody id="campaigns-table"></tbody></table>
        </div>
      </div>
    </div>

    <!-- INBOX -->
    <div id="panel-inbox" class="panel">
      <div class="panel-header">
        <div><div class="panel-title">Email Inbox</div><div class="panel-subtitle">Monitored every 2 min · AI classifies &amp; auto-replies</div></div>
        <button class="btn-ghost btn-sm" onclick="loadInbox()">↻ Refresh</button>
      </div>
      <div class="panel-body" style="padding-top:0">
        <div class="card" style="overflow:hidden">
          <table><thead><tr><th>From</th><th>Subject</th><th>Intent</th><th>AI Replied</th><th>Received</th><th>Preview</th></tr></thead>
          <tbody id="inbox-table"></tbody></table>
        </div>
      </div>
    </div>

    <!-- LIVE CHATS -->
    <div id="panel-livechats" class="panel">
      <div class="panel-header"><div><div class="panel-title">Live Customer Chats</div><div class="panel-subtitle">Real-time WebSocket from your business page</div></div></div>
      <div class="panel-body">
        <div style="display:grid;grid-template-columns:280px 1fr;gap:20px;height:calc(100vh - 165px)">
          <div>
            <div style="font-size:11px;font-family:'DM Mono',monospace;color:var(--muted);margin-bottom:10px;text-transform:uppercase;letter-spacing:.06em">Active Rooms</div>
            <div id="chat-rooms-list"><div style="color:var(--muted);font-size:13px;font-family:'DM Mono',monospace">No active chats</div></div>
          </div>
          <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;display:flex;flex-direction:column;overflow:hidden">
            <div id="live-chat-header" style="padding:16px 20px;border-bottom:1px solid var(--border);font-weight:700;font-size:14px;color:var(--muted)">Select a chat →</div>
            <div id="live-chat-msgs" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px"></div>
            <div style="padding:12px 16px;border-top:1px solid var(--border);display:flex;gap:8px">
              <input id="live-reply-inp" class="inp" placeholder="Reply as owner..." style="flex:1" onkeydown="if(event.key==='Enter')sendLiveReply()">
              <button class="btn-sm btn-accent" onclick="sendLiveReply()">Send</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- SETTINGS -->
    <div id="panel-settings" class="panel">
      <div class="panel-header"><div><div class="panel-title">Settings</div><div class="panel-subtitle">Business info &amp; appearance</div></div></div>
      <div class="panel-body">
        <div style="max-width:600px;display:flex;flex-direction:column;gap:24px">
          <div class="card" style="padding:24px">
            <div style="font-size:15px;font-weight:700;margin-bottom:16px">Business Info</div>
            <div class="form-grid">
              <div class="form-row"><label class="form-label">Name</label><input class="inp" id="s-biz-name"></div>
              <div class="form-row"><label class="form-label">Industry</label><input class="inp" id="s-biz-industry"></div>
            </div>
            <div class="form-row"><label class="form-label">Tagline</label><input class="inp" id="s-biz-tagline"></div>
            <div class="form-row"><label class="form-label">Description</label><textarea class="inp" id="s-biz-desc" rows="3"></textarea></div>
            <button class="btn-sm btn-accent" style="margin-top:12px" onclick="saveBizInfo()">Save Info</button>
          </div>
          <div class="card" style="padding:24px">
            <div style="font-size:15px;font-weight:700;margin-bottom:4px">📧 Email System</div>
            <div style="font-size:13px;font-family:'DM Mono',monospace;color:var(--muted);margin-top:8px;line-height:1.8">
              Email is managed via server configuration.<br>
              All campaigns and auto-replies are sent from the server's configured SMTP account.<br>
              <span id="smtp-status" style="color:var(--green)">Checking...</span>
            </div>
          </div>
          <div class="card" style="padding:24px">
            <div style="font-size:15px;font-weight:700;margin-bottom:4px">Business Page</div>
            <div style="font-size:12px;font-family:'DM Mono',monospace;color:var(--muted);margin-bottom:16px">AI-generated public page with live customer chat</div>
            <div id="page-url-display" style="font-family:'DM Mono',monospace;font-size:13px;color:var(--muted);margin-bottom:12px">No page generated yet</div>
            <button class="btn-sm btn-accent" onclick="generatePage()">✦ Generate with AI</button>
          </div>
          <div class="card" style="padding:24px">
            <div style="font-size:15px;font-weight:700;margin-bottom:8px">🎨 Appearance</div>
            <div style="display:flex;gap:12px;align-items:center">
              <button class="btn-sm btn-outline" onclick="setTheme('dark')">🌙 Dark Mode</button>
              <button class="btn-sm btn-outline" onclick="setTheme('light')">☀️ Light Mode</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- MODALS -->
<div id="modal-add-product" class="modal-overlay" style="display:none" onclick="closeModalOutside(event,'modal-add-product')">
  <div class="modal-box">
    <div class="modal-title">Add Product / Service</div>
    <div class="form-grid">
      <div class="form-row" style="grid-column:1/-1"><label class="form-label">Name *</label><input class="inp" id="p-name"></div>
      <div class="form-row"><label class="form-label">Price</label><input class="inp" id="p-price" type="number" placeholder="0.00"></div>
      <div class="form-row"><label class="form-label">Currency</label><input class="inp" id="p-currency" value="USD"></div>
      <div class="form-row"><label class="form-label">Category</label><input class="inp" id="p-category" placeholder="e.g. Software"></div>
      <div class="form-row"><label class="form-label">Image URL</label><input class="inp" id="p-img" placeholder="https://..."></div>
      <div class="form-row" style="grid-column:1/-1"><label class="form-label">Description</label><textarea class="inp" id="p-desc" rows="3"></textarea></div>
    </div>
    <div style="display:flex;gap:10px;margin-top:16px">
      <button class="btn-sm btn-outline" onclick="hideModal('modal-add-product')">Cancel</button>
      <button class="btn-sm btn-accent" onclick="saveProduct()">Add Product</button>
    </div>
  </div>
</div>

<div id="modal-add-contact" class="modal-overlay" style="display:none" onclick="closeModalOutside(event,'modal-add-contact')">
  <div class="modal-box">
    <div class="modal-title">Add Contact</div>
    <div class="form-grid">
      <div class="form-row"><label class="form-label">Name</label><input class="inp" id="c-name"></div>
      <div class="form-row"><label class="form-label">Email *</label><input class="inp" id="c-email" type="email"></div>
      <div class="form-row"><label class="form-label">Company</label><input class="inp" id="c-company"></div>
      <div class="form-row"><label class="form-label">Phone</label><input class="inp" id="c-phone"></div>
    </div>
    <div class="form-row"><label class="form-label">Notes</label><textarea class="inp" id="c-notes" rows="2"></textarea></div>
    <div style="display:flex;gap:10px;margin-top:16px">
      <button class="btn-sm btn-outline" onclick="hideModal('modal-add-contact')">Cancel</button>
      <button class="btn-sm btn-accent" onclick="saveContact()">Add Contact</button>
    </div>
  </div>
</div>

<!-- IMPORT CONTACTS MODAL -->
<div id="modal-import-contacts" class="modal-overlay" style="display:none" onclick="closeModalOutside(event,'modal-import-contacts')">
  <div class="modal-box">
    <div class="modal-title">📥 Import Contacts</div>
    <div style="display:flex;gap:12px;margin-bottom:20px">
      <button class="btn-sm btn-accent" id="import-tab-type" onclick="switchImportTab('type')">Type / Paste</button>
      <button class="btn-sm btn-outline" id="import-tab-csv" onclick="switchImportTab('csv')">Upload CSV</button>
      <button class="btn-sm btn-outline" id="import-tab-pool" onclick="switchImportTab('pool')">Shared Pool</button>
    </div>

    <!-- Type/Paste -->
    <div id="import-type-panel">
      <div class="form-label" style="margin-bottom:8px">Type or paste emails (one per line, or comma-separated)</div>
      <div class="email-chips" id="email-chips-box" onclick="$el('chip-input').focus()">
        <input id="chip-input" placeholder="type email then press Enter..." onkeydown="handleChipKey(event)" oninput="handleChipInput(event)">
      </div>
      <div style="font-size:12px;color:var(--muted);font-family:'DM Mono',monospace;margin-top:6px">
        Press Enter after each email · Paste multiple with commas · <span id="chip-count">0</span> added
      </div>
    </div>

    <!-- CSV Upload -->
    <div id="import-csv-panel" style="display:none">
      <div class="drop-zone" id="csv-drop-zone" onclick="$el('csv-file-inp').click()"
           ondragover="event.preventDefault();this.classList.add('dragover')"
           ondragleave="this.classList.remove('dragover')"
           ondrop="handleCsvDrop(event)">
        📄 Drop your CSV file here or click to browse<br>
        <span style="font-size:11px;opacity:.7">Needs columns: email (required), name, company</span>
        <input type="file" id="csv-file-inp" accept=".csv" style="display:none" onchange="handleCsvFile(this)">
      </div>
      <div id="csv-preview" style="margin-top:12px;font-size:13px;color:var(--green);font-family:'DM Mono',monospace"></div>
    </div>

    <!-- Shared Pool -->
    <div id="import-pool-panel" style="display:none">
      <div style="font-size:13px;color:var(--muted);font-family:'DM Mono',monospace;margin-bottom:12px">
        These are contacts from the shared MarketMe ecosystem. Importing adds them to your contacts.
      </div>
      <div id="pool-list" style="max-height:260px;overflow-y:auto"></div>
    </div>

    <div style="display:flex;gap:10px;margin-top:20px">
      <button class="btn-sm btn-outline" onclick="hideModal('modal-import-contacts')">Cancel</button>
      <button class="btn-sm btn-accent" onclick="doImport()">Import Contacts</button>
    </div>
  </div>
</div>

<div id="modal-find-leads" class="modal-overlay" style="display:none" onclick="closeModalOutside(event,'modal-find-leads')">
  <div class="modal-box">
    <div class="modal-title">🔍 Find Leads with AI Browser</div>
    <div style="font-size:13px;font-family:'DM Mono',monospace;color:var(--muted);margin-bottom:20px">Playwright opens a headless browser, searches Bing/DuckDuckGo, and extracts emails. Falls back to shared CSV pool.</div>
    <div class="form-row"><label class="form-label">Industry / Niche</label><input class="inp" id="fl-industry" placeholder="e.g. SaaS startups"></div>
    <div class="form-row"><label class="form-label">Location</label><input class="inp" id="fl-location" placeholder="e.g. Lagos, Nigeria"></div>
    <div class="form-row"><label class="form-label">Keywords</label><input class="inp" id="fl-keywords" placeholder="e.g. marketing, growth"></div>
    <div style="display:flex;gap:10px;margin-top:16px">
      <button class="btn-sm btn-outline" onclick="hideModal('modal-find-leads')">Cancel</button>
      <button class="btn-sm btn-accent" onclick="startLeadSearch()">🔍 Start Search</button>
    </div>
  </div>
</div>

<!-- CAMPAIGN MODAL - multi-email -->
<div id="modal-add-campaign" class="modal-overlay" style="display:none" onclick="closeModalOutside(event,'modal-add-campaign')">
  <div class="modal-box">
    <div class="modal-title">📣 New Email Campaign</div>
    <div class="form-row"><label class="form-label">Campaign Name *</label><input class="inp" id="cp-name"></div>
    <div class="form-row"><label class="form-label">Subject Line *</label><input class="inp" id="cp-subject"></div>
    <div class="form-row"><label class="form-label">Email Body</label><textarea class="inp" id="cp-body" rows="5" placeholder="Write your email body..."></textarea></div>
    <div class="form-row"><label class="form-label">Schedule (leave blank to save as draft)</label><input class="inp" id="cp-schedule" type="datetime-local"></div>

    <!-- Audience -->
    <div style="border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:14px">
      <div style="font-size:13px;font-weight:700;margin-bottom:12px">Recipients</div>
      <div class="form-row">
        <label class="form-label">From your contacts</label>
        <select class="inp" id="cp-audience">
          <option value="all">All Contacts</option>
          <option value="new">New contacts only</option>
          <option value="interested">Interested contacts only</option>
          <option value="none">None (use extra emails only)</option>
        </select>
      </div>
      <div class="form-row">
        <label class="form-label">Extra emails (type + Enter, or paste comma-separated)</label>
        <div class="email-chips" id="campaign-chips-box" onclick="$el('campaign-chip-input').focus()" style="min-height:52px">
          <input id="campaign-chip-input" placeholder="add emails..." onkeydown="handleCampaignChipKey(event)" oninput="handleCampaignChipInput(event)">
        </div>
        <div style="margin-top:6px;display:flex;gap:8px;align-items:center">
          <label class="btn-sm btn-purple" style="cursor:pointer">
            📄 Upload CSV<input type="file" id="campaign-csv" accept=".csv" style="display:none" onchange="handleCampaignCsv(this)">
          </label>
          <span id="campaign-chip-count" style="font-size:12px;color:var(--muted);font-family:'DM Mono',monospace">0 extra emails</span>
        </div>
      </div>
    </div>

    <div style="display:flex;gap:10px;margin-top:4px">
      <button class="btn-sm btn-outline" onclick="hideModal('modal-add-campaign')">Cancel</button>
      <button class="btn-sm btn-outline" onclick="saveCampaign(false)">Save Draft</button>
      <button class="btn-sm btn-accent" onclick="saveCampaign(true)">🚀 Send Now</button>
    </div>
  </div>
</div>

<div id="notification" class="notification" style="display:none">
  <div class="notify-title" id="notif-title"></div>
  <div class="notify-body" id="notif-body"></div>
</div>

<script>
// ── STATE ─────────────────────────────────────────────────────────
const S={
  token:localStorage.getItem('mm_token'),
  user:JSON.parse(localStorage.getItem('mm_user')||'null'),
  biz:null,chatHistory:[],
  sessionId:(crypto.randomUUID?crypto.randomUUID():Math.random().toString(36).slice(2)),
  socket:null,voiceActive:false,audioCtx:null,nextPlayTime:0,
  voiceMediaStream:null,voiceProcessor:null,activeRoom:null,contacts:[],
  pendingImageB64:null,pendingImageMime:'image/jpeg',
  importMode:'type',importChips:[],campaignChips:[],_voiceRestartTimer:null
};

function $v(id){return document.getElementById(id)?.value?.trim()||'';}
function $el(id){return document.getElementById(id);}
function escHtml(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

async function api(method,path,body){
  const opts={method,headers:{'Content-Type':'application/json',...(S.token?{'Authorization':'Bearer '+S.token}:{})}};
  if(body) opts.body=JSON.stringify(body);
  try{const r=await fetch(path,opts);return r.json();}catch(e){return {error:e.message};}
}

let _nt;
function notify(title,body,dur=5000){
  $el('notif-title').textContent=title;$el('notif-body').textContent=body;
  const n=$el('notification');n.style.display='block';n.style.opacity='1';
  clearTimeout(_nt);_nt=setTimeout(()=>{n.style.opacity='0';setTimeout(()=>n.style.display='none',300);},dur);
}

// ── THEME ─────────────────────────────────────────────────────────
function setTheme(mode){
  document.documentElement.setAttribute('data-theme',mode);
  localStorage.setItem('mm_theme',mode);
  $el('theme-btn').textContent=mode==='dark'?'🌙 Dark':'☀️ Light';
}
function toggleTheme(){
  const cur=document.documentElement.getAttribute('data-theme')||'dark';
  setTheme(cur==='dark'?'light':'dark');
}
(function(){const t=localStorage.getItem('mm_theme')||'dark';setTheme(t);})();

// ── AUTH ──────────────────────────────────────────────────────────
function authTab(t){
  document.querySelectorAll('.auth-tab').forEach((el,i)=>el.classList.toggle('active',['login','register','miracle'][i]===t));
  $el('form-login').style.display=t==='login'?'':'none';
  $el('form-register').style.display=t==='register'?'':'none';
  $el('form-miracle').style.display=t==='miracle'?'':'none';
}
async function doLogin(){
  const r=await api('POST','/api/auth/login',{email:$v('l-email'),password:$v('l-pass')});
  if(r.error){$el('l-err').textContent=r.error;return;}onAuth(r);
}
async function doRegister(){
  const r=await api('POST','/api/auth/register',{name:$v('r-name'),email:$v('r-email'),password:$v('r-pass')});
  if(r.error){$el('r-err').textContent=r.error;return;}onAuth(r);
}
async function doMiracle(){
  const r=await api('POST','/api/auth/miracle/request',{email:$v('m-email')});
  $el('m-msg').textContent=r.message||'Miracle link sent!';
}
async function checkMiracleToken(){
  const p=new URLSearchParams(window.location.search);const t=p.get('miracle');if(!t)return false;
  const r=await api('POST','/api/auth/miracle/verify',{token:t});
  if(r.token){onAuth(r);history.replaceState({},'','/');return true;}return false;
}
function onAuth(r){
  S.token=r.token;S.user=r.user;
  localStorage.setItem('mm_token',r.token);localStorage.setItem('mm_user',JSON.stringify(r.user));
  $el('auth').style.display='none';
  if(r.user.business_id)loadApp();else showOnboard();
}
function logout(){localStorage.clear();location.reload();}

// ── ONBOARDING ────────────────────────────────────────────────────
let obData={};
function showOnboard(){$el('auth').style.display='none';$el('onboard').style.display='flex';}
async function obFinish(){
  const name=$v('ob-name');if(!name){$el('ob-err').textContent='Business name required';return;}
  obData={name,industry:$v('ob-industry'),tagline:$v('ob-tagline'),description:$v('ob-desc')};
  const r=await api('POST','/api/business',obData);
  if(r.error){notify('Error',r.error);return;}
  S.biz=r.business;S.user.business_id=r.business.id;
  localStorage.setItem('mm_user',JSON.stringify(S.user));
  $el('onboard').style.display='none';initApp();
}

// ── APP INIT ──────────────────────────────────────────────────────
async function loadApp(){
  $el('auth').style.display='none';
  const r=await api('GET','/api/business');
  if(r.business){S.biz=r.business;initApp();}else showOnboard();
}
function initApp(){
  $el('app').style.display='flex';
  if(S.biz){
    $el('sb-biz-name').textContent=S.biz.name;populateSettings();
    if(S.biz.page_url)$el('page-url-display').innerHTML='<a href="'+S.biz.page_url+'" target="_blank" style="color:var(--accent)">'+location.origin+S.biz.page_url+'</a>';
  }
  // Check SMTP
  api('GET','/api/business').then(r=>{
    if(r.business?.smtp_configured)
      $el('smtp-status').textContent='✓ Email system active';
    else $el('smtp-status').textContent='⚠ Configure SMTP in server .env';
  });
  initSocket();loadProducts();loadContacts();loadCampaigns();loadInbox();
  addMsg('assistant',
    'Hello! I\'m your MarketMe Agent powered by Nova 2 Lite.\n\n'+
    'I can:\n• Navigate the app — try "show me contacts" or "go to campaigns"\n'+
    '• Analyse images — click 📎 and upload one\n'+
    '• Find leads, add products, launch campaigns\n'+
    '• Switch themes — try "dark mode" or "light mode"\n'+
    '• Open any form — try "add a new product"\n\nWhat shall we do today?',null,null);
}

// ── SOCKET ────────────────────────────────────────────────────────
function initSocket(){
  S.socket=io();
  if(S.biz)S.socket.emit('join_biz',{business_id:S.biz.id});
  S.socket.on('inbox_update',d=>{
    $el('inbox-dot').style.display='block';
    notify('📥 '+d.intent.toUpperCase(),d.from+' (AI replied: '+(d.ai_replied?'yes':'no')+')');
    loadInbox();});
  S.socket.on('campaign_update',d=>{notify('📣 Campaign Sent',d.name+' — '+d.sent+' delivered');loadCampaigns();});
  S.socket.on('leads_found',d=>{notify('🔍 Leads Found',d.count+' contacts added');loadContacts();});
  S.socket.on('new_chat_request',d=>{notify('⚡ New Chat',d.customer_name+' wants to chat');addChatRoom(d);});
  S.socket.on('voice_ready',()=>updateVoiceStatus('Session starting...'));
  S.socket.on('voice_session_active',()=>updateVoiceStatus('🟢 Listening — speak now'));
  S.socket.on('voice_audio_out',d=>playAudioChunk(d.audio));
  S.socket.on('voice_transcript',d=>{const tb=$el('transcript-box');tb.textContent+='\nAgent: '+d.text;tb.scrollTop=tb.scrollHeight;});
  S.socket.on('voice_error',d=>{updateVoiceStatus('Error: '+(typeof d.error==='object'?JSON.stringify(d.error):d.error));stopVoice();});
  S.socket.on('live_message',d=>renderLiveMsg(d));
  S.socket.on('owner_joined',d=>{ $el('live-chat-header').textContent='Chat — Room '+d.room_id; });
}

// ── NAVIGATION ────────────────────────────────────────────────────
function showPanel(name){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
  $el('panel-'+name).classList.add('active');
  const el=document.querySelector('[data-panel="'+name+'"]');if(el)el.classList.add('active');
  if(name==='inbox')$el('inbox-dot').style.display='none';
}

// ── CHAT ──────────────────────────────────────────────────────────
function handleImgUpload(input){
  const file=input.files[0];if(!file)return;
  const reader=new FileReader();
  reader.onload=e=>{
    const data=e.target.result;
    const b64=data.split(',')[1];
    S.pendingImageB64=b64;S.pendingImageMime=file.type||'image/jpeg';
    const wrap=$el('img-preview-wrap');
    wrap.style.display='block';
    wrap.innerHTML='<img class="img-preview" src="'+data+'" title="Click to remove" onclick="clearImg()">';
    notify('📎 Image ready','Will be sent with your next message');
  };reader.readAsDataURL(file);
}
function clearImg(){S.pendingImageB64=null;$el('img-preview-wrap').style.display='none';$el('img-preview-wrap').innerHTML='';$el('img-upload').value='';}

async function sendChat(){
  const inp=$el('chat-inp');const txt=inp.value.trim();
  if(!txt&&!S.pendingImageB64)return;
  inp.value='';
  const imgB64=S.pendingImageB64;const imgMime=S.pendingImageMime;
  if(imgB64)clearImg();

  // Show user message
  const userContent=imgB64?'[Image] '+(txt||'Analyse this image'):txt;
  addMsg('user',userContent,null,null,imgB64?('data:'+imgMime+';base64,'+imgB64):null);
  S.chatHistory.push({role:'user',content:txt||'Analyse this image'});

  const typId='typ-'+Date.now();
  $el('chat-area').insertAdjacentHTML('beforeend','<div id="'+typId+'" class="msg assistant"><div class="msg-bubble"><div class="typing-dot"><span></span><span></span><span></span></div></div></div>');
  scrollChat();

  const body={messages:S.chatHistory,session_id:S.sessionId};
  if(imgB64){body.image_b64=imgB64;body.image_mime=imgMime;}

  const r=await api('POST','/api/chat',body);
  $el(typId)?.remove();
  addMsg('assistant',r.content||'Sorry, empty response.',r.intent,r.action,null);
  if(r.content)S.chatHistory.push({role:'assistant',content:r.content});
  if(S.chatHistory.length>40)S.chatHistory=S.chatHistory.slice(-40);

  // Handle actions
  if(r.action){
    const t=r.action.type||r.intent;
    // UI actions
    if(t==='navigate'){showPanel(r.action.panel);}
    if(t==='open_modal'){showModal('modal-'+r.action.modal);}
    if(t==='toggle_theme'){setTheme(r.action.mode||'dark');}
    if(t==='show_notification'){notify(r.action.title||'',r.action.message||'');}
    // Business actions
    if(t==='product_added'){loadProducts();notify('✅ Product Added',r.action.name||'');}
    if(t==='campaign_drafted'){loadCampaigns();notify('📣 Campaign Drafted',r.action.name||'');}
    if(t==='lead_search_started')notify('🔍 Lead Search Started','Running in background');
    if(t==='followup_scheduled')notify('⏰ Follow-up Scheduled','Sending in '+r.action.delay_hours+'h');
    if(t==='page_generated'){
      if(S.biz)S.biz.page_url=r.action.url;
      $el('page-url-display').innerHTML='<a href="'+r.action.url+'" target="_blank" style="color:var(--accent)">'+location.origin+r.action.url+'</a>';
      notify('🌐 Page Live!',location.origin+r.action.url);}
  }
}

function addMsg(role,content,intent,action,imgSrc){
  const time=new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  let ih=intent?'<div class="intent-badge">⚡ '+escHtml(intent)+'</div>':'';
  let imgHtml=imgSrc?'<img class="msg-img" src="'+escHtml(imgSrc)+'">':'';
  let ah='';
  if(action&&(action.type||action.panel)){
    const t=action.type||intent;
    const msgs={
      navigate:'🗂 Navigating to '+escHtml(action.panel||''),
      open_modal:'📋 Opening '+escHtml(action.modal||'')+' form',
      toggle_theme:'🎨 Theme switched to '+escHtml(action.mode||''),
      product_added:'✅ Product "'+escHtml(action.name||'')+'" added',
      campaign_drafted:'📣 Campaign drafted — review in Campaigns',
      lead_search_started:'🔍 Searching for leads in background...',
      followup_scheduled:'⏰ Follow-up scheduled',
      page_generated:'🌐 Page: <a href="'+escHtml(action.url||'')+'" target="_blank" style="color:var(--accent)">View</a>',
      live_chat:'⚡ Chat room: '+(action.room_id||'none'),
    };
    if(msgs[t])ah='<div class="action-card">'+msgs[t]+'</div>';
  }
  $el('chat-area').insertAdjacentHTML('beforeend',
    '<div class="msg '+role+'">'+imgHtml+'<div class="msg-bubble">'+escHtml(content)+'</div>'+ih+ah+'<div class="msg-time">'+time+'</div></div>');
  scrollChat();
}
function scrollChat(){const a=$el('chat-area');a.scrollTop=a.scrollHeight;}
function clearChat(){
  $el('chat-area').innerHTML='';S.chatHistory=[];
  S.sessionId=(crypto.randomUUID?crypto.randomUUID():Math.random().toString(36).slice(2));
  addMsg('assistant','Chat cleared. How can I help?',null,null,null);
}

// ── VOICE ─────────────────────────────────────────────────────────
function toggleVoiceInChat(){S.voiceActive?stopVoice():startVoice();$el('voice-toggle-btn').classList.toggle('active',!S.voiceActive);}
function toggleVoice(){S.voiceActive?stopVoice():startVoice();}
function startVoice(){
  if(S.voiceActive)return;
  navigator.mediaDevices.getUserMedia({audio:true}).then(stream=>{
    S.voiceActive=true;S.voiceMediaStream=stream;
    S.audioCtx=new(window.AudioContext||window.webkitAudioContext)();
    S.audioCtx.resume();S.nextPlayTime=0;
    const src=S.audioCtx.createMediaStreamSource(stream);
    const proc=S.audioCtx.createScriptProcessor(2048,1,1);
    proc.onaudioprocess=e=>{
      if(!S.voiceActive)return;
      const raw=e.inputBuffer.getChannelData(0);
      const ratio=S.audioCtx.sampleRate/24000;
      const out=new Int16Array(Math.floor(raw.length/ratio));
      for(let i=0;i<out.length;i++){const s=Math.max(-1,Math.min(1,raw[Math.floor(i*ratio)]));out[i]=s<0?s*32768:s*32767;}
      S.socket.emit('voice_audio',{audio:btoa(String.fromCharCode(...new Uint8Array(out.buffer)))});
    };
    src.connect(proc);proc.connect(S.audioCtx.destination);S.voiceProcessor=proc;
    $el('mic-btn').classList.add('recording');
    $el('transcript-box').textContent='Session started...\n';
    updateVoiceStatus('Connecting to Nova Sonic...');
    S.socket.emit('voice_start',{business_id:S.biz?.id});
    // Auto-restart before 8 min limit
    S._voiceRestartTimer=setTimeout(()=>{
      if(S.voiceActive){updateVoiceStatus('Refreshing session...');stopVoice();setTimeout(startVoice,1200);}
    },7*60*1000);
  }).catch(e=>notify('Microphone Error',e.message));
}
function stopVoice(){
  clearTimeout(S._voiceRestartTimer);S.voiceActive=false;
  if(S.voiceMediaStream){S.voiceMediaStream.getTracks().forEach(t=>t.stop());S.voiceMediaStream=null;}
  if(S.voiceProcessor){S.voiceProcessor.disconnect();S.voiceProcessor=null;}
  if(S.audioCtx){S.audioCtx.close();S.audioCtx=null;}
  S.socket.emit('voice_stop');
  $el('mic-btn').classList.remove('recording');$el('voice-toggle-btn').classList.remove('active');
  updateVoiceStatus('Session ended. Click to start again.');
}
function updateVoiceStatus(msg){$el('voice-status').textContent=msg;}
function playAudioChunk(b64){
  if(!S.audioCtx)return;
  try{
    const bin=atob(b64),buf=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)buf[i]=bin.charCodeAt(i);
    const i16=new Int16Array(buf.buffer);const f32=new Float32Array(i16.length);
    for(let i=0;i<i16.length;i++)f32[i]=i16[i]/32768;
    const ab=S.audioCtx.createBuffer(1,f32.length,24000);ab.getChannelData(0).set(f32);
    const src=S.audioCtx.createBufferSource();src.buffer=ab;src.connect(S.audioCtx.destination);
    const now=S.audioCtx.currentTime;if(S.nextPlayTime<now)S.nextPlayTime=now;
    src.start(S.nextPlayTime);S.nextPlayTime+=ab.duration;
  }catch(e){}
}

// ── PRODUCTS ──────────────────────────────────────────────────────
async function loadProducts(){
  const r=await api('GET','/api/products');const g=$el('products-grid');
  if(!r.products||!r.products.length){g.innerHTML='<div style="color:var(--muted);font-family:\'DM Mono\',monospace;font-size:13px;grid-column:1/-1;padding:24px 0">No products yet — add one or ask the agent!</div>';return;}
  g.innerHTML=r.products.map(p=>`<div class="product-card">
    ${p.image_url?'<img src="'+escHtml(p.image_url)+'" style="width:100%;height:120px;object-fit:cover;border-radius:8px;margin-bottom:12px" onerror="this.style.display=\'none\'">':''}
    <div class="product-name">${escHtml(p.name)}</div>
    <div class="product-price">${escHtml(p.currency)} ${Number(p.price).toFixed(2)}</div>
    <div class="product-desc">${escHtml(p.description||'')}</div>
    ${p.category?'<div class="product-category">'+escHtml(p.category)+'</div>':''}
    <button class="btn-sm btn-danger" style="margin-top:12px" onclick="delProduct(${p.id})">Delete</button>
  </div>`).join('');
}
async function saveProduct(){
  const r=await api('POST','/api/products',{name:$v('p-name'),description:$v('p-desc'),
    price:parseFloat($v('p-price')||0),currency:$v('p-currency')||'USD',
    category:$v('p-category'),image_url:$v('p-img')});
  if(r.product){hideModal('modal-add-product');loadProducts();notify('✅ Product Added',r.product.name);}
  else if(r.error)notify('Error',r.error);
}
async function delProduct(id){if(confirm('Delete?')){await api('DELETE','/api/products/'+id);loadProducts();}}

// ── CONTACTS ──────────────────────────────────────────────────────
async function loadContacts(){
  const r=await api('GET','/api/contacts');S.contacts=r.contacts||[];
  $el('contacts-count').textContent=(S.contacts.length||0)+' total contacts';
  const tb=$el('contacts-table');
  if(!S.contacts.length){tb.innerHTML='<tr><td colspan="6" style="color:var(--muted);font-family:\'DM Mono\',monospace;text-align:center;padding:24px">No contacts yet</td></tr>';return;}
  tb.innerHTML=S.contacts.map(c=>`<tr>
    <td style="font-weight:600">${escHtml(c.name||'—')}</td>
    <td style="font-family:'DM Mono',monospace;font-size:12px">${escHtml(c.email)}</td>
    <td style="color:var(--muted)">${escHtml(c.company||'—')}</td>
    <td><span class="status-badge status-${escHtml(c.status)}">${escHtml(c.status)}</span></td>
    <td><span class="status-badge status-${escHtml(c.source)}">${escHtml(c.source)}</span></td>
    <td><button class="btn-sm btn-danger" onclick="delContact(${c.id})">✕</button></td>
  </tr>`).join('');
}
async function saveContact(){
  const r=await api('POST','/api/contacts',{name:$v('c-name'),email:$v('c-email'),
    company:$v('c-company'),phone:$v('c-phone'),notes:$v('c-notes')});
  if(r.contact){hideModal('modal-add-contact');loadContacts();}else if(r.error)notify('Error',r.error);
}
async function delContact(id){if(confirm('Delete?')){await api('DELETE','/api/contacts/'+id);loadContacts();}}

// ── IMPORT CONTACTS ───────────────────────────────────────────────
function switchImportTab(tab){
  S.importMode=tab;
  ['type','csv','pool'].forEach(t=>{
    $el('import-'+t+'-panel').style.display=t===tab?'':'none';
    $el('import-tab-'+t).className='btn-sm '+(t===tab?'btn-accent':'btn-outline');
  });
  if(tab==='pool') loadPoolContacts();
}
async function loadPoolContacts(){
  const r=await api('GET','/api/contacts/csv-pool');
  const list=$el('pool-list');
  if(!r.contacts||!r.contacts.length){list.innerHTML='<div style="color:var(--muted);font-size:13px">No shared contacts yet</div>';return;}
  list.innerHTML=r.contacts.map(c=>`<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)">
    <div><div style="font-size:13px;font-weight:600">${escHtml(c.name||c.email)}</div>
    <div style="font-size:11px;color:var(--muted);font-family:'DM Mono',monospace">${escHtml(c.email)} ${c.company?'· '+escHtml(c.company):''}</div></div>
    <input type="checkbox" value="${escHtml(c.email)}" class="pool-check" style="width:16px;height:16px">
  </div>`).join('');
}

// Chip input for import modal
function handleChipKey(e){
  if(e.key==='Enter'||e.key===','||e.key===';'){
    e.preventDefault();addChip($el('chip-input').value,'import');
  }
}
function handleChipInput(e){
  const val=e.target.value;
  if(val.includes(',')){ val.split(',').forEach(v=>addChip(v,'import')); e.target.value=''; }
}
function handleCampaignChipKey(e){
  if(e.key==='Enter'||e.key===','||e.key===';'){e.preventDefault();addChip($el('campaign-chip-input').value,'campaign');}
}
function handleCampaignChipInput(e){
  const val=e.target.value;
  if(val.includes(',')){ val.split(',').forEach(v=>addChip(v,'campaign')); e.target.value=''; }
}
function addChip(raw, target){
  const email=raw.trim().replace(/[,;]/g,'');
  if(!email||!email.includes('@'))return;
  if(target==='import'){
    if(S.importChips.includes(email))return;
    S.importChips.push(email);
    renderChips('email-chips-box',S.importChips,'import');
    $el('chip-input').value='';
    $el('chip-count').textContent=S.importChips.length;
  } else {
    if(S.campaignChips.includes(email))return;
    S.campaignChips.push(email);
    renderChips('campaign-chips-box',S.campaignChips,'campaign');
    $el('campaign-chip-input').value='';
    $el('campaign-chip-count').textContent=S.campaignChips.length+' extra emails';
  }
}
function removeChip(email, target){
  if(target==='import'){S.importChips=S.importChips.filter(e=>e!==email);renderChips('email-chips-box',S.importChips,'import');$el('chip-count').textContent=S.importChips.length;}
  else{S.campaignChips=S.campaignChips.filter(e=>e!==email);renderChips('campaign-chips-box',S.campaignChips,'campaign');$el('campaign-chip-count').textContent=S.campaignChips.length+' extra emails';}
}
function renderChips(boxId, chips, target){
  const box=$el(boxId);const inp=box.querySelector('input');
  box.querySelectorAll('.email-chip').forEach(c=>c.remove());
  chips.forEach(email=>{
    const chip=document.createElement('div');chip.className='email-chip';
    chip.innerHTML=escHtml(email)+'<span class="chip-x" onclick="removeChip(\''+escHtml(email)+'\',\''+target+'\')">✕</span>';
    box.insertBefore(chip,inp);
  });
}
function handleCsvFile(input){
  const file=input.files[0];if(!file)return;
  const reader=new FileReader();
  reader.onload=e=>{
    const lines=e.target.result.split('\n');
    const emails=[];
    lines.forEach(line=>{
      const match=line.match(/[\w.+\-]+@[\w\-]+\.\w{2,6}/);
      if(match)emails.push(match[0]);
    });
    $el('csv-preview').textContent=`Found ${emails.length} emails in CSV`;
    emails.forEach(em=>addChip(em,'import'));
  };reader.readAsText(file);
}
function handleCsvDrop(e){
  e.preventDefault();$el('csv-drop-zone').classList.remove('dragover');
  const file=e.dataTransfer.files[0];if(!file)return;
  const reader=new FileReader();
  reader.onload=ev=>{
    const emails=[];
    ev.target.result.split('\n').forEach(line=>{
      const m=line.match(/[\w.+\-]+@[\w\-]+\.\w{2,6}/);if(m)emails.push(m[0]);
    });
    $el('csv-preview').textContent=`Found ${emails.length} emails`;
    emails.forEach(em=>addChip(em,'import'));
  };reader.readAsText(file);
}
function handleCampaignCsv(input){
  const file=input.files[0];if(!file)return;
  const reader=new FileReader();
  reader.onload=e=>{
    e.target.result.split('\n').forEach(line=>{
      const m=line.match(/[\w.+\-]+@[\w\-]+\.\w{2,6}/);if(m)addChip(m[0],'campaign');
    });
  };reader.readAsText(file);
}
async function doImport(){
  let emails=[];
  if(S.importMode==='type'||S.importMode==='csv'){
    emails=S.importChips.map(e=>({email:e}));
  } else if(S.importMode==='pool'){
    document.querySelectorAll('.pool-check:checked').forEach(cb=>emails.push({email:cb.value}));
  }
  if(!emails.length){notify('Nothing to import','Add some emails first');return;}
  const r=await api('POST','/api/contacts/import',{emails});
  if(r.ok){
    hideModal('modal-import-contacts');loadContacts();
    notify('✅ Imported',r.added+' new contacts added');
    S.importChips=[];renderChips('email-chips-box',[],'import');
  } else notify('Error',r.error||'Import failed');
}
async function startLeadSearch(){
  await api('POST','/api/chat',{
    messages:[{role:'user',content:'Find business leads in '+$v('fl-industry')+' based in '+$v('fl-location')+' related to '+$v('fl-keywords')}],
    session_id:S.sessionId});
  hideModal('modal-find-leads');notify('🔍 Lead Search Running','Using Playwright + shared pool fallback');
}

// ── CAMPAIGNS ─────────────────────────────────────────────────────
async function loadCampaigns(){
  const r=await api('GET','/api/campaigns');const tb=$el('campaigns-table');
  if(!r.campaigns||!r.campaigns.length){tb.innerHTML='<tr><td colspan="6" style="color:var(--muted);text-align:center;padding:24px;font-family:\'DM Mono\',monospace">No campaigns yet</td></tr>';return;}
  tb.innerHTML=r.campaigns.map(c=>`<tr>
    <td style="font-weight:600">${escHtml(c.name||'—')}</td>
    <td style="font-family:'DM Mono',monospace;font-size:12px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(c.subject||'—')}</td>
    <td><span class="status-badge status-${escHtml(c.status)}">${escHtml(c.status)}</span></td>
    <td style="font-family:'DM Mono',monospace">${c.sent_count||0}</td>
    <td style="font-family:'DM Mono',monospace;font-size:12px;color:var(--muted)">${c.scheduled_at?new Date(c.scheduled_at).toLocaleString():'—'}</td>
    <td>${c.status==='draft'?'<button class="btn-sm btn-accent" onclick="sendCampaignNow('+c.id+')">Send</button>':''}</td>
  </tr>`).join('');
}
async function saveCampaign(sendNow){
  await loadContacts();
  const audience=$v('cp-audience');
  let ids=[];
  if(audience==='all') ids=S.contacts.map(c=>c.id);
  else if(audience==='new') ids=S.contacts.filter(c=>c.status==='new').map(c=>c.id);
  else if(audience==='interested') ids=S.contacts.filter(c=>c.status==='interested').map(c=>c.id);
  const sched=$v('cp-schedule');
  const r=await api('POST','/api/campaigns',{name:$v('cp-name'),subject:$v('cp-subject'),
    body_plain:$v('cp-body'),contact_ids:ids,raw_emails:S.campaignChips,
    send_now:sendNow,scheduled_at:(!sendNow&&sched)?sched:null});
  if(r.campaign){
    hideModal('modal-add-campaign');loadCampaigns();
    S.campaignChips=[];renderChips('campaign-chips-box',[],'campaign');
    notify(sendNow?'📣 Campaign Queued':'📋 Draft Saved',r.campaign.name);
  } else if(r.error)notify('Error',r.error);
}
async function sendCampaignNow(id){
  await api('POST','/api/campaigns/'+id+'/send');loadCampaigns();notify('📣 Queued','Sending shortly');
}

// ── INBOX ─────────────────────────────────────────────────────────
async function loadInbox(){
  const r=await api('GET','/api/email-threads');const tb=$el('inbox-table');
  if(!r.threads||!r.threads.length){tb.innerHTML='<tr><td colspan="6" style="color:var(--muted);font-family:\'DM Mono\',monospace;text-align:center;padding:24px">No threads yet — agent monitors inbox every 2 min</td></tr>';return;}
  tb.innerHTML=r.threads.map(t=>`<tr>
    <td style="font-family:'DM Mono',monospace;font-size:12px">${escHtml(t.from_email)}</td>
    <td style="font-weight:600;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(t.subject||'—')}</td>
    <td><span class="status-badge intent-${escHtml(t.intent)}">${escHtml(t.intent)}</span></td>
    <td>${t.ai_auto_reply?'<span style="color:var(--green);font-family:\'DM Mono\',monospace;font-size:11px">✓ Replied</span>':'<span style="color:var(--muted);font-family:\'DM Mono\',monospace;font-size:11px">—</span>'}</td>
    <td style="font-family:'DM Mono',monospace;font-size:12px;color:var(--muted)">${new Date(t.received_at).toLocaleString()}</td>
    <td style="font-size:12px;color:var(--muted);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml((t.body_snippet||'').slice(0,70))}</td>
  </tr>`).join('');
}

// ── LIVE CHATS ────────────────────────────────────────────────────
function addChatRoom(d){
  const list=$el('chat-rooms-list');
  if(list.querySelector('div[style*="color"]'))list.innerHTML='';
  const item=document.createElement('div');item.className='room-item';item.id='room-'+d.room_id;
  item.innerHTML='<div class="room-name">'+escHtml(d.customer_name)+'</div><div class="room-status">'+escHtml(d.customer_email||'')+'</div>';
  item.onclick=()=>joinRoom(d.room_id,d.customer_name);list.prepend(item);
}
function joinRoom(roomId,name){
  S.activeRoom=roomId;document.querySelectorAll('.room-item').forEach(r=>r.classList.remove('active-room'));
  const item=$el('room-'+roomId);if(item)item.classList.add('active-room');
  $el('live-chat-header').textContent='Chat with '+name+' (connecting...)';
  $el('live-chat-msgs').innerHTML='';S.socket.emit('owner_join_chat',{room_id:roomId});
}
function renderLiveMsg(d){
  const el=$el('live-chat-msgs');if(!el)return;
  const isOwner=d.sender===(S.user?.name||'Owner');
  el.insertAdjacentHTML('beforeend','<div style="background:'+(isOwner?'#f59e0b1a':'var(--card)')+
    ';border:1px solid '+(isOwner?'#f59e0b44':'var(--border)')+
    ';border-radius:10px;padding:10px 14px;font-size:13px;align-self:'+(isOwner?'flex-end':'flex-start')+
    ';max-width:80%"><b style="font-size:11px;color:var(--muted)">'+escHtml(d.sender)+'</b><br>'+escHtml(d.text)+'</div>');
  el.scrollTop=el.scrollHeight;
}
function sendLiveReply(){
  if(!S.activeRoom)return;const inp=$el('live-reply-inp');if(!inp.value.trim())return;
  const sender=S.user?.name||'Owner';
  S.socket.emit('live_message',{room_id:S.activeRoom,sender:sender,text:inp.value});
  renderLiveMsg({sender:sender,text:inp.value});inp.value='';
}

// ── SETTINGS ──────────────────────────────────────────────────────
function populateSettings(){
  if(!S.biz)return;
  $el('s-biz-name').value=S.biz.name||'';$el('s-biz-industry').value=S.biz.industry||'';
  $el('s-biz-tagline').value=S.biz.tagline||'';$el('s-biz-desc').value=S.biz.description||'';
}
async function saveBizInfo(){
  const r=await api('PUT','/api/business/settings',{name:$v('s-biz-name'),industry:$v('s-biz-industry'),
    tagline:$v('s-biz-tagline'),description:$v('s-biz-desc')});
  if(r.ok){S.biz=r.business;$el('sb-biz-name').textContent=S.biz.name;notify('✅ Saved','Business info updated');}
  else notify('Error',r.error||'Save failed');
}
async function generatePage(){
  notify('⏳ Generating','AI building your page...',10000);
  const r=await api('POST','/api/business/generate-page');
  if(r.ok){if(S.biz)S.biz.page_url=r.url;
    $el('page-url-display').innerHTML='<a href="'+r.url+'" target="_blank" style="color:var(--accent)">'+location.origin+r.url+'</a>';
    notify('🌐 Page Live!',location.origin+r.url);}
  else notify('Error',r.error||'Failed');
}

// ── MODALS ────────────────────────────────────────────────────────
function showModal(id){
  $el(id).style.display='flex';
  // Reset import state when opening
  if(id==='modal-import-contacts'){
    S.importChips=[];renderChips('email-chips-box',[],'import');
    $el('chip-count').textContent='0';
    switchImportTab('type');
  }
  if(id==='modal-add-campaign'){
    S.campaignChips=[];renderChips('campaign-chips-box',[],'campaign');
    $el('campaign-chip-count').textContent='0 extra emails';
  }
}
function hideModal(id){$el(id).style.display='none';}
function closeModalOutside(e,id){if(e.target.id===id)hideModal(id);}

// ── BOOT ──────────────────────────────────────────────────────────
(async function boot(){
  if(await checkMiracleToken())return;
  if(S.token&&S.user)loadApp();else $el('auth').style.display='flex';
})();
</script>
</body>
</html>"""

# ── ENTRY POINT ───────────────────────────────────────────────────
if __name__ == "__main__":
    csv_ensure()
    with app.app_context():
        db.create_all()
        log.info("✦ MarketMe v2.0 DB initialised")
    log.info("✦ Starting on http://0.0.0.0:5000")
    log.info("✦ Agent: celery -A app:celery_app worker --beat -l info")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)