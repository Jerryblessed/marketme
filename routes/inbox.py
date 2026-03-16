from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions import db
from models import User, EmailThread
from utils.serializers import s_thread

inbox_bp = Blueprint("inbox", __name__, url_prefix="/api")


@inbox_bp.route("/email-threads", methods=["GET"])
@jwt_required()
def route_threads():
    uid  = int(get_jwt_identity())
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "Session expired, please log in again"}), 401
    threads = (
        EmailThread.query
        .filter_by(business_id=user.business_id)
        .order_by(EmailThread.received_at.desc())
        .limit(100)
        .all()
    )
    return jsonify({"threads": [s_thread(t) for t in threads]})
