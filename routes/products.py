from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions import db
from models import User, Product
from utils.serializers import s_product

products_bp = Blueprint("products", __name__, url_prefix="/api")


@products_bp.route("/products", methods=["GET", "POST"])
@jwt_required()
def route_products():
    uid  = int(get_jwt_identity())
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "Session expired, please log in again"}), 401

    if request.method == "GET":
        return jsonify({
            "products": [
                s_product(p)
                for p in Product.query.filter_by(business_id=user.business_id).all()
            ]
        })

    d = request.get_json()
    p = Product(
        business_id=user.business_id,
        name=d["name"],
        description=d.get("description", ""),
        price=float(d.get("price", 0)),
        currency=d.get("currency", "USD"),
        category=d.get("category", ""),
        image_url=d.get("image_url", ""),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"product": s_product(p)})


@products_bp.route("/products/<int:pid>", methods=["DELETE"])
@jwt_required()
def delete_product(pid):
    p = Product.query.get_or_404(pid)
    db.session.delete(p)
    db.session.commit()
    return jsonify({"ok": True})
