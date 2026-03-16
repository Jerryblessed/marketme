import uuid
import threading
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, create_refresh_token

from extensions import db
from models import User
from utils.email_utils import smtp_send
from config import APP_URL

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.route("/register", methods=["POST"])
def register():
    d = request.get_json()
    if User.query.filter_by(email=d["email"]).first():
        return jsonify({"error": "Email already registered"}), 409
    u = User(email=d["email"], name=d.get("name", ""))
    u.set_password(d["password"])
    db.session.add(u)
    db.session.commit()
    return jsonify({
        "token": create_access_token(identity=str(u.id)),
        "user":  {"id": u.id, "email": u.email, "name": u.name, "business_id": u.business_id},
    })


@auth_bp.route("/login", methods=["POST"])
def login():
    d = request.get_json()
    u = User.query.filter_by(email=d["email"]).first()
    if not u or not u.check_password(d.get("password", "")):
        return jsonify({"error": "Invalid credentials"}), 401
    return jsonify({
        "token":   create_access_token(identity=str(u.id)),
        "refresh": create_refresh_token(identity=str(u.id)),
        "user":    {"id": u.id, "email": u.email, "name": u.name, "business_id": u.business_id},
    })


@auth_bp.route("/miracle/request", methods=["POST"])
def miracle_request():
    d = request.get_json()
    u = User.query.filter_by(email=d.get("email", "")).first()
    if u:
        token           = str(uuid.uuid4())
        u.miracle_token  = token
        u.miracle_expiry = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()
        body = (
            f"Your MarketMe miracle login link:\n\n"
            f"{APP_URL}/?miracle={token}\n\n"
            f"Expires in 1 hour.\n\n— MarketMe"
        )
        threading.Thread(
            target=smtp_send,
            args=(u.email, "✦ MarketMe — Your miracle login link", body),
            daemon=True,
        ).start()
    return jsonify({"message": "If that email is registered, a miracle link has been sent."})


@auth_bp.route("/miracle/verify", methods=["POST"])
def miracle_verify():
    d = request.get_json()
    u = User.query.filter_by(miracle_token=d.get("token", "")).first()
    if not u or not u.miracle_expiry or u.miracle_expiry < datetime.utcnow():
        return jsonify({"error": "Invalid or expired miracle link"}), 401
    u.miracle_token  = None
    u.miracle_expiry = None
    db.session.commit()
    return jsonify({
        "token": create_access_token(identity=str(u.id)),
        "user":  {"id": u.id, "email": u.email, "name": u.name, "business_id": u.business_id},
    })
