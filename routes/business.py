import re
import threading
from datetime import datetime

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions import db
from models import User, Business, Product, Contact
from utils.serializers import s_biz
from utils.nova_utils import generate_business_page
from utils.csv_utils import csv_load

business_bp = Blueprint("business", __name__, url_prefix="/api")


def _seed_contacts_from_csv(business_id):
    """Runs in a background thread after first business creation."""
    from app import app
    with app.app_context():
        for row in csv_load():
            if not Contact.query.filter_by(
                business_id=business_id, email=row["email"]
            ).first():
                db.session.add(
                    Contact(
                        business_id=business_id,
                        email=row["email"],
                        name=row.get("name", ""),
                        company=row.get("company", ""),
                        phone=row.get("phone", ""),
                        notes=row.get("notes", ""),
                        source="csv",
                    )
                )
        db.session.commit()


@business_bp.route("/business", methods=["GET", "POST"])
@jwt_required()
def route_business():
    uid  = int(get_jwt_identity())
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "Session expired, please log in again"}), 401

    if request.method == "GET":
        if not user.business_id:
            return jsonify({"business": None})
        return jsonify({"business": s_biz(db.session.get(Business, user.business_id))})

    d    = request.get_json()
    slug = re.sub(r"[^a-z0-9]+", "-", d["name"].lower().strip()).strip("-")
    base, n = slug, 1
    while Business.query.filter_by(slug=slug).first():
        slug = f"{base}-{n}"
        n   += 1

    biz = Business(
        name=d["name"],
        slug=slug,
        tagline=d.get("tagline", ""),
        description=d.get("description", ""),
        industry=d.get("industry", ""),
    )
    db.session.add(biz)
    db.session.flush()
    user.business_id = biz.id
    db.session.commit()

    # Seed contacts from shared CSV pool in background
    threading.Thread(target=_seed_contacts_from_csv, args=(biz.id,), daemon=True).start()
    return jsonify({"business": s_biz(biz)})


@business_bp.route("/business/settings", methods=["PUT"])
@jwt_required()
def business_settings():
    uid  = int(get_jwt_identity())
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "Session expired, please log in again"}), 401
    biz = db.session.get(Business, user.business_id)
    if not biz:
        return jsonify({"error": "No business"}), 404
    d = request.get_json()
    for field in ("name", "tagline", "description", "industry", "website"):
        if field in d and d[field] is not None:
            setattr(biz, field, d[field])
    db.session.commit()
    return jsonify({"ok": True, "business": s_biz(biz)})


@business_bp.route("/business/generate-page", methods=["POST"])
@jwt_required()
def gen_page():
    uid  = int(get_jwt_identity())
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "Session expired, please log in again"}), 401
    biz = db.session.get(Business, user.business_id)
    if not biz:
        return jsonify({"error": "No business"}), 404
    biz.page_html    = generate_business_page(
        biz, Product.query.filter_by(business_id=biz.id, active=True).all()
    )
    biz.page_updated = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "url": f"/biz/{biz.slug}"})
