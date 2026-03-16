import json
from datetime import datetime

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions import db
from models import User, Campaign
from utils.serializers import s_campaign

campaigns_bp = Blueprint("campaigns", __name__, url_prefix="/api")


@campaigns_bp.route("/campaigns", methods=["GET", "POST"])
@jwt_required()
def route_campaigns():
    uid  = int(get_jwt_identity())
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "Session expired, please log in again"}), 401

    if request.method == "GET":
        return jsonify({
            "campaigns": [
                s_campaign(c)
                for c in Campaign.query
                    .filter_by(business_id=user.business_id)
                    .order_by(Campaign.created_at.desc())
                    .all()
            ]
        })

    d     = request.get_json()
    sched = datetime.fromisoformat(d["scheduled_at"]) if d.get("scheduled_at") else None
    cp    = Campaign(
        business_id=user.business_id,
        name=d.get("name", ""),
        subject=d.get("subject", ""),
        body_html=d.get("body_html", ""),
        body_plain=d.get("body_plain", ""),
        contact_ids=json.dumps(d.get("contact_ids", [])),
        raw_emails=json.dumps(d.get("raw_emails", [])),
        status="scheduled" if (d.get("send_now") or sched) else "draft",
        scheduled_at=sched or (datetime.utcnow() if d.get("send_now") else None),
    )
    db.session.add(cp)
    db.session.commit()
    return jsonify({"campaign": s_campaign(cp)})


@campaigns_bp.route("/campaigns/<int:cid>/send", methods=["POST"])
@jwt_required()
def send_campaign_now(cid):
    cp             = Campaign.query.get_or_404(cid)
    cp.status      = "scheduled"
    cp.scheduled_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})
