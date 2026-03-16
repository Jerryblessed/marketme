import re
import json
import logging
from openai import OpenAI
from config import NOVA_API_KEY, NOVA_BASE_URL

log = logging.getLogger("marketme.nova")

AGENT_SYSTEM = """You are MarketMe Agent — an expert AI marketing assistant with FULL CONTROL of the app.

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


def nova_client():
    return OpenAI(api_key=NOVA_API_KEY, base_url=NOVA_BASE_URL)


def chat_with_nova(messages, biz=None, image_b64=None, image_mime="image/jpeg"):
    client = nova_client()
    sys_content = AGENT_SYSTEM
    if biz:
        sys_content += (
            f"\n\nBusiness: {biz.name} | Industry: {biz.industry} | "
            f"{biz.description or ''}"
        )
    full_msgs = [{"role": "system", "content": sys_content}]

    for i, msg in enumerate(messages):
        if i == len(messages) - 1 and image_b64:
            full_msgs.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": msg.get("content", "Analyse this image")},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{image_mime};base64,{image_b64}"
                    }},
                ],
            })
        else:
            full_msgs.append(msg)

    # Disable web grounding for pure UI / navigation commands
    nav_keywords = [
        "go to", "show me", "open", "navigate", "dark mode",
        "light mode", "switch to", "add a",
    ]
    use_grounding = not any(
        kw in (messages[-1].get("content", "") if messages else "").lower()
        for kw in nav_keywords
    )

    try:
        resp = client.chat.completions.create(
            model="nova-2-lite-v1",
            messages=full_msgs,
            max_tokens=1000,
            temperature=0.7,
            extra_body={"system_tools": ["nova_grounding"]} if use_grounding else {},
        )
        raw = resp.choices[0].message.content or ""
        intent, params, content = None, {}, raw
        m = re.search(
            r'\{"intent"\s*:[^{}]+(?:"params"\s*:\s*\{[^{}]*\})?\s*\}',
            raw, re.DOTALL,
        )
        if m:
            try:
                data   = json.loads(m.group())
                intent = data.get("intent")
                params = data.get("params", {})
                content = raw[: m.start()].strip()
            except Exception:
                pass
        return {"content": content, "intent": intent, "params": params}
    except Exception as e:
        log.error(f"Nova chat: {e}")
        return {"content": f"I ran into an issue: {e}", "intent": None, "params": {}}


def generate_business_page(biz, products):
    client = nova_client()
    prods = "\n".join(
        [f"- {p.name}: {p.description} (${p.price} {p.currency})" for p in products]
    ) or "No products yet."
    prompt = (
        f"Create complete modern HTML business landing page.\n"
        f"Business:{biz.name}\nTagline:{biz.tagline}\n"
        f"Description:{biz.description}\nIndustry:{biz.industry}\n"
        f"Products:\n{prods}\n\n"
        f"- Full HTML with Tailwind CDN\n"
        f"- Professional design\n"
        f"- Sections: hero, about, products, CTA\n"
        f"- Chat button calling window.openLiveChat()\n"
        f"- Mobile responsive\n"
        f"- Return ONLY raw HTML"
    )
    try:
        resp = client.chat.completions.create(
            model="nova-2-lite-v1",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
        )
        html = resp.choices[0].message.content or ""
        return re.sub(r"^```html\n?", "", html).rstrip("`").strip()
    except Exception as e:
        return f"<html><body><h1>{biz.name}</h1><p>Error: {e}</p></body></html>"


def classify_email_intent(subject, body):
    try:
        resp = nova_client().chat.completions.create(
            model="nova-2-lite-v1",
            max_tokens=5,
            temperature=0,
            messages=[{
                "role": "user",
                "content": (
                    f"ONE word only: agreed/declined/interested/question/other\n"
                    f"Subject:{subject}\nBody:{body[:300]}"
                ),
            }],
        )
        w = resp.choices[0].message.content.strip().lower()
        return w if w in ("agreed", "declined", "interested", "question") else "other"
    except Exception:
        return "other"


def draft_auto_reply(subject, body, biz_name, contact_name=""):
    try:
        resp = nova_client().chat.completions.create(
            model="nova-2-lite-v1",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    f"As marketing agent for {biz_name}, write a warm professional reply "
                    f"to {contact_name or 'this customer'}.\n"
                    f"Subject:{subject}\nThey wrote:{body[:400]}\n"
                    f"Return ONLY the reply body text, no subject line."
                ),
            }],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"auto_reply draft: {e}")
        return ""


def draft_campaign_email(biz, params):
    try:
        prompt = (
            f"Draft marketing email for {biz.name}.\n"
            f"Campaign:{params.get('campaign_name','')}\n"
            f"Product:{params.get('product_name','')}\n"
            f"Tone:{params.get('tone','professional')}\n"
            f"Target:{params.get('target_audience','general')}\n"
            f'Return ONLY JSON: {{"subject":"...","body_plain":"...","body_html":"..."}}'
        )
        resp = nova_client().chat.completions.create(
            model="nova-2-lite-v1",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = re.sub(r"^```json\n?", "", resp.choices[0].message.content or "").rstrip("`").strip()
        return json.loads(raw)
    except Exception:
        return {
            "subject":    f"News from {biz.name}",
            "body_plain": f"Hi,\n\nWe have exciting news.\n\nBest,\n{biz.name}",
            "body_html":  "",
        }
