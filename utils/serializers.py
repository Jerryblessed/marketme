import json
from config import SMTP_USER


def s_biz(b):
    return {
        "id":               b.id,
        "name":             b.name,
        "slug":             b.slug,
        "tagline":          b.tagline,
        "description":      b.description,
        "industry":         b.industry,
        "website":          b.website,
        "smtp_configured":  bool(SMTP_USER),
        "page_url":         f"/biz/{b.slug}" if b.page_html else None,
        "created_at":       b.created_at.isoformat(),
    }


def s_product(p):
    return {
        "id":          p.id,
        "name":        p.name,
        "description": p.description,
        "price":       p.price,
        "currency":    p.currency,
        "category":    p.category,
        "image_url":   p.image_url,
        "active":      p.active,
    }


def s_contact(c):
    return {
        "id":         c.id,
        "name":       c.name,
        "email":      c.email,
        "company":    c.company,
        "phone":      c.phone,
        "status":     c.status,
        "source":     c.source,
        "notes":      c.notes,
        "created_at": c.created_at.isoformat(),
    }


def s_campaign(c):
    return {
        "id":           c.id,
        "name":         c.name,
        "subject":      c.subject,
        "status":       c.status,
        "sent_count":   c.sent_count,
        "body_plain":   c.body_plain,
        "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
        "sent_at":      c.sent_at.isoformat() if c.sent_at else None,
        "contact_ids":  json.loads(c.contact_ids or "[]"),
        "raw_emails":   json.loads(c.raw_emails or "[]"),
        "created_at":   c.created_at.isoformat(),
    }


def s_thread(t):
    return {
        "id":            t.id,
        "subject":       t.subject,
        "from_email":    t.from_email,
        "direction":     t.direction,
        "intent":        t.intent,
        "body_snippet":  t.body_snippet,
        "ai_auto_reply": t.ai_auto_reply,
        "ai_reply_body": t.ai_reply_body,
        "received_at":   t.received_at.isoformat(),
    }
