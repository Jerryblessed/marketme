import json
from datetime import datetime

from extensions import db
from models import Product, Contact, Campaign, LiveChatRoom
from utils.nova_utils import draft_campaign_email, generate_business_page


def handle_intent(intent, params, user, biz):
    # ── UI intents — handled on the frontend, just pass through ──
    if intent in ("navigate", "open_modal", "toggle_theme", "show_notification"):
        return {"type": intent, **params}

    # ── add_product ───────────────────────────────────────────────
    if intent == "add_product":
        p = Product(
            business_id=biz.id,
            name=params.get("name", "New Product"),
            description=params.get("description", ""),
            price=float(params.get("price", 0)),
            category=params.get("category", ""),
        )
        db.session.add(p)
        db.session.commit()
        return {"type": "product_added", "product_id": p.id, "name": p.name}

    # ── launch_campaign ───────────────────────────────────────────
    if intent == "launch_campaign":
        draft = draft_campaign_email(biz, params)
        contacts_all = Contact.query.filter_by(business_id=biz.id).all()
        cp = Campaign(
            business_id=biz.id,
            name=params.get("campaign_name", f"Campaign {datetime.utcnow().date()}"),
            subject=draft.get("subject", ""),
            body_html=draft.get("body_html", ""),
            body_plain=draft.get("body_plain", ""),
            contact_ids=json.dumps([c.id for c in contacts_all]),
            status="draft",
        )
        db.session.add(cp)
        db.session.commit()
        return {"type": "campaign_drafted", "campaign_id": cp.id, "name": cp.name}

    # ── find_leads ────────────────────────────────────────────────
    if intent == "find_leads":
        from tasks import scrape_leads_task
        scrape_leads_task.delay(
            biz.id,
            params.get("industry", biz.industry or ""),
            params.get("location", ""),
            params.get("keywords", ""),
        )
        return {"type": "lead_search_started"}

    # ── schedule_followup ─────────────────────────────────────────
    if intent == "schedule_followup":
        from tasks import send_followup_email_task
        dh = float(params.get("delay_hours", 24))
        send_followup_email_task.apply_async(
            args=[biz.id, params.get("contact_email", ""), params.get("message_hint", "")],
            countdown=int(dh * 3600),
        )
        return {"type": "followup_scheduled", "delay_hours": dh}

    # ── generate_page ─────────────────────────────────────────────
    if intent == "generate_page":
        prods = Product.query.filter_by(business_id=biz.id, active=True).all()
        biz.page_html = generate_business_page(biz, prods)
        biz.page_updated = datetime.utcnow()
        db.session.commit()
        return {"type": "page_generated", "url": f"/biz/{biz.slug}"}

    # ── connect_customer ──────────────────────────────────────────
    if intent == "connect_customer":
        room = (
            LiveChatRoom.query
            .filter_by(business_id=biz.id, customer_email=params.get("contact_email", ""))
            .filter(LiveChatRoom.status != "closed")
            .first()
        )
        return {"type": "live_chat", "room_id": room.room_id if room else None}

    return {}
