import csv
import io

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions import db
from models import User, Contact
from utils.serializers import s_contact
from utils.csv_utils import csv_append, csv_load

contacts_bp = Blueprint("contacts", __name__, url_prefix="/api")


@contacts_bp.route("/contacts", methods=["GET", "POST"])
@jwt_required()
def route_contacts():
    uid  = int(get_jwt_identity())
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "Session expired, please log in again"}), 401

    if request.method == "GET":
        return jsonify({
            "contacts": [
                s_contact(c)
                for c in Contact.query
                    .filter_by(business_id=user.business_id)
                    .order_by(Contact.created_at.desc())
                    .all()
            ]
        })

    d = request.get_json()
    c = Contact(
        business_id=user.business_id,
        email=d["email"],
        name=d.get("name", ""),
        company=d.get("company", ""),
        phone=d.get("phone", ""),
        notes=d.get("notes", ""),
        source="manual",
    )
    db.session.add(c)
    db.session.commit()
    csv_append(d.get("name", ""), d["email"], d.get("company", ""),
               d.get("phone", ""), d.get("notes", ""))
    return jsonify({"contact": s_contact(c)})


@contacts_bp.route("/contacts/<int:cid>", methods=["DELETE"])
@jwt_required()
def delete_contact(cid):
    c = Contact.query.get_or_404(cid)
    db.session.delete(c)
    db.session.commit()
    return jsonify({"ok": True})


@contacts_bp.route("/contacts/import", methods=["POST"])
@jwt_required()
def import_contacts():
    """Import from CSV file upload or JSON list of emails."""
    uid  = int(get_jwt_identity())
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "Session expired, please log in again"}), 401
    added = 0

    # ── CSV file upload ───────────────────────────────────────────
    if "file" in request.files:
        f       = request.files["file"]
        content = f.read().decode("utf-8", errors="replace")
        reader  = csv.DictReader(io.StringIO(content))
        for row in reader:
            email   = (row.get("email") or row.get("Email") or "").strip()
            if not email or "@" not in email:
                continue
            name    = (row.get("name") or row.get("Name") or "").strip()
            company = (row.get("company") or row.get("Company") or "").strip()
            if not Contact.query.filter_by(
                business_id=user.business_id, email=email
            ).first():
                db.session.add(
                    Contact(business_id=user.business_id, email=email,
                            name=name, company=company, source="csv")
                )
                csv_append(name, email, company)
                added += 1
        db.session.commit()
        return jsonify({"ok": True, "added": added})

    # ── JSON list of raw emails ───────────────────────────────────
    d   = request.get_json()
    raw = d.get("emails", [])
    for item in raw:
        email = (item.get("email", item) if isinstance(item, dict) else item).strip()
        if not email or "@" not in email:
            continue
        name = (item.get("name", "") if isinstance(item, dict) else "").strip()
        if not Contact.query.filter_by(
            business_id=user.business_id, email=email
        ).first():
            db.session.add(
                Contact(business_id=user.business_id, email=email,
                        name=name, source="import")
            )
            csv_append(name, email)
            added += 1
    db.session.commit()
    return jsonify({"ok": True, "added": added})


@contacts_bp.route("/contacts/csv-pool", methods=["GET"])
@jwt_required()
def csv_pool():
    """Return the shared CSV contacts pool."""
    return jsonify({"contacts": csv_load()})
