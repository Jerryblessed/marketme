import uuid

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions import db
from models import User, Business, ChatLog
from utils.nova_utils import chat_with_nova
from utils.intent_handler import handle_intent

chat_bp = Blueprint("chat", __name__, url_prefix="/api")


@chat_bp.route("/chat", methods=["POST"])
@jwt_required()
def route_chat():
    uid  = int(get_jwt_identity())
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "Session expired, please log in again"}), 401

    biz        = db.session.get(Business, user.business_id) if user.business_id else None
    d          = request.get_json()
    session_id = d.get("session_id", str(uuid.uuid4()))
    messages   = d.get("messages", [])
    image_b64  = d.get("image_b64")
    image_mime = d.get("image_mime", "image/jpeg")

    # Log incoming user message
    db.session.add(ChatLog(
        business_id=user.business_id,
        session_id=session_id,
        role="user",
        content=messages[-1]["content"] if messages else "",
    ))
    db.session.commit()

    result = chat_with_nova(messages, biz, image_b64, image_mime)

    # Dispatch intent
    action = {}
    if result["intent"]:
        if result["intent"] in ("navigate", "open_modal", "toggle_theme", "show_notification"):
            action = {"type": result["intent"], **result["params"]}
        elif biz:
            action = handle_intent(result["intent"], result["params"], user, biz)

    # Log assistant reply
    db.session.add(ChatLog(
        business_id=user.business_id,
        session_id=session_id,
        role="assistant",
        content=result["content"],
        intent_detected=result["intent"],
    ))
    db.session.commit()

    return jsonify({
        "content": result["content"],
        "intent":  result["intent"],
        "params":  result["params"],
        "action":  action,
    })
