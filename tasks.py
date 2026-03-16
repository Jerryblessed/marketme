import re
import json
import logging
from datetime import datetime

from extensions import celery_app, db, socketio
from utils.csv_utils import csv_append
from utils.email_utils import smtp_send, fetch_unseen_emails
from utils.nova_utils import (classify_email_intent, draft_auto_reply,
                               nova_client)
from utils.scraper import scrape_leads
from config import SMTP_USER

log = logging.getLogger("marketme.tasks")


def _get_app():
    """Late import to avoid circular dependency with app.py."""
    from app import app
    return app


# ── IMAP monitor ─────────────────────────────────────────────────
@celery_app.task(name="marketme.monitor_inbox")
def monitor_inbox():
    from models import Business, Contact, EmailThread
    app = _get_app()
    with app.app_context():
        bizs = Business.query.all()
        if not bizs:
            return
        emails = fetch_unseen_emails()
        log.info(f"Processing {len(emails)} emails for {len(bizs)} businesses")

        for em in emails:
            from_addr = (
                re.findall(r"[\w.+\-]+@[\w\-]+\.\w{2,6}", em["from"]) or [em["from"]]
            )[0]
            # Skip our own outbound
            if from_addr.lower() == SMTP_USER.lower():
                continue

            for biz in bizs:
                if EmailThread.query.filter_by(
                    message_id=em["message_id"], business_id=biz.id
                ).first():
                    continue

                intent  = classify_email_intent(em["subject"], em["body"])
                contact = Contact.query.filter_by(
                    business_id=biz.id, email=from_addr
                ).first()

                # Update contact status
                sm = {
                    "agreed":      "agreed",
                    "declined":    "declined",
                    "interested":  "interested",
                    "question":    "contacted",
                }
                if contact and intent in sm:
                    contact.status = sm[intent]

                # Auto-reply for agreed / interested / question / other
                ai_reply = ""
                if intent in ("agreed", "interested", "question", "other"):
                    cname = contact.name if contact else from_addr
                    ai_reply = draft_auto_reply(em["subject"], em["body"], biz.name, cname)
                    if ai_reply:
                        ok = smtp_send(from_addr, f"Re: {em['subject']}", ai_reply)
                        log.info(f"Auto-reply to {from_addr}: {'sent' if ok else 'failed'}")
                        csv_append(cname, from_addr, notes=f"Replied via MarketMe - intent:{intent}")

                thread = EmailThread(
                    business_id=biz.id,
                    contact_id=contact.id if contact else None,
                    message_id=em["message_id"],
                    in_reply_to=em.get("in_reply_to", ""),
                    subject=em["subject"],
                    from_email=from_addr,
                    body_snippet=em["body"][:500],
                    direction="inbound",
                    intent=intent,
                    ai_auto_reply=bool(ai_reply),
                    ai_reply_body=ai_reply,
                )
                db.session.add(thread)
                db.session.commit()

                socketio.emit(
                    "inbox_update",
                    {
                        "business_id": biz.id,
                        "from":        from_addr,
                        "subject":     em["subject"],
                        "intent":      intent,
                        "ai_replied":  bool(ai_reply),
                    },
                    room=f"biz_{biz.id}",
                )
                break  # one business per email


# ── Campaign sender ───────────────────────────────────────────────
@celery_app.task(name="marketme.process_campaigns")
def process_campaigns():
    from models import Business, Contact, Campaign, EmailThread
    app = _get_app()
    with app.app_context():
        now = datetime.utcnow()
        due = Campaign.query.filter(
            Campaign.status == "scheduled",
            Campaign.scheduled_at <= now,
        ).all()

        for cp in due:
            cp.status = "sending"
            db.session.commit()

            biz  = db.session.get(Business, cp.business_id)
            sent = 0

            # Send to DB contacts
            for cid in json.loads(cp.contact_ids or "[]"):
                c = db.session.get(Contact, cid)
                if not c or not c.email:
                    continue
                if smtp_send(c.email, cp.subject, cp.body_plain, cp.body_html):
                    sent += 1
                    c.status = "contacted"
                    db.session.add(
                        EmailThread(
                            business_id=biz.id,
                            contact_id=c.id,
                            campaign_id=cp.id,
                            subject=cp.subject,
                            from_email=SMTP_USER,
                            body_snippet=cp.body_plain[:300],
                            direction="outbound",
                        )
                    )

            # Send to raw / extra emails
            for email in json.loads(cp.raw_emails or "[]"):
                if smtp_send(email, cp.subject, cp.body_plain, cp.body_html):
                    sent += 1
                    csv_append("", email, notes="Campaign recipient")

            cp.status    = "sent"
            cp.sent_at   = datetime.utcnow()
            cp.sent_count = sent
            db.session.commit()

            socketio.emit(
                "campaign_update",
                {"campaign_id": cp.id, "name": cp.name, "sent": sent, "status": "sent"},
                room=f"biz_{biz.id}",
            )


# ── Lead scraper ──────────────────────────────────────────────────
@celery_app.task(name="marketme.scrape_leads_task")
def scrape_leads_task(business_id, industry, location, keywords):
    from models import Contact
    app = _get_app()
    with app.app_context():
        leads = scrape_leads(industry, location, keywords)
        added = 0
        for lead in leads:
            if not Contact.query.filter_by(
                business_id=business_id, email=lead["email"]
            ).first():
                db.session.add(
                    Contact(
                        business_id=business_id,
                        email=lead["email"],
                        name=lead.get("name", ""),
                        company=lead.get("company", ""),
                        notes=lead.get("notes", ""),
                        source="scrape",
                    )
                )
                csv_append(
                    lead.get("name", ""),
                    lead["email"],
                    company=lead.get("company", ""),
                    notes=lead.get("notes", ""),
                )
                added += 1
        db.session.commit()
        socketio.emit(
            "leads_found",
            {"count": added, "industry": industry, "location": location},
            room=f"biz_{business_id}",
        )


# ── Follow-up email ───────────────────────────────────────────────
@celery_app.task(name="marketme.send_followup_email")
def send_followup_email_task(business_id, contact_email, message_hint):
    import re, json
    from models import Business
    app = _get_app()
    with app.app_context():
        biz = db.session.get(Business, business_id)
        if not biz:
            return
        try:
            resp = nova_client().chat.completions.create(
                model="nova-2-lite-v1",
                max_tokens=400,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Follow-up email for {biz.name}. Hint: {message_hint}. "
                        f'Return ONLY JSON: {{"subject":"...","body":"..."}}'
                    ),
                }],
            )
            raw  = re.sub(r"^```json\n?", "", resp.choices[0].message.content or "").rstrip("`").strip()
            data = json.loads(raw)
            smtp_send(contact_email, data["subject"], data["body"])
        except Exception as e:
            log.error(f"Follow-up: {e}")
