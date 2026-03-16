import uuid
import ssl
import json
import asyncio
import threading
import logging
from datetime import datetime

import websockets as ws_lib
from flask import request
from flask_socketio import emit, join_room

from extensions import db, socketio
from models import Business, LiveChatRoom
from utils.csv_utils import csv_append
from config import NOVA_API_KEY, NOVA_WS_URL, APP_URL

log = logging.getLogger("marketme.sockets")

# Active voice sessions: {socket_id: {active, queue, api_key}}
_voice_sessions: dict = {}


def register_socket_events(sio):
    """Bind all SocketIO event handlers. Called from app.py after socketio.init_app()."""

    # ── Business room ─────────────────────────────────────────────
    @sio.on("join_biz")
    def sio_join_biz(data):
        bid = data.get("business_id")
        if bid:
            join_room(f"biz_{bid}")
            emit("joined", {"room": f"biz_{bid}"})

    # ── Customer joins from public biz page ───────────────────────
    @sio.on("customer_join")
    def sio_customer_join(data):
        biz = Business.query.filter_by(slug=data.get("slug", "")).first()
        if not biz:
            return
        room_id = str(uuid.uuid4())[:8]
        room = LiveChatRoom(
            business_id=biz.id,
            room_id=room_id,
            customer_name=data.get("name", "Guest"),
            customer_email=data.get("email", ""),
        )
        db.session.add(room)
        db.session.commit()
        join_room(room_id)

        emit(
            "new_chat_request",
            {
                "room_id":        room_id,
                "customer_name":  room.customer_name,
                "customer_email": room.customer_email,
            },
            room=f"biz_{biz.id}",
        )
        emit("chat_ready", {"room_id": room_id})
        csv_append(data.get("name", ""), data.get("email", ""), notes="Live chat visitor")

    # ── Owner joins a live chat room ──────────────────────────────
    @sio.on("owner_join_chat")
    def sio_owner_join(data):
        room_id = data.get("room_id")
        join_room(room_id)
        room = LiveChatRoom.query.filter_by(room_id=room_id).first()
        if room:
            room.status = "active"
            db.session.commit()
        emit("owner_joined", {"room_id": room_id}, room=room_id)

    # ── Live chat message relay ───────────────────────────────────
    @sio.on("live_message")
    def sio_live_msg(data):
        emit(
            "live_message",
            {
                "sender": data.get("sender", "Unknown"),
                "text":   data.get("text", ""),
                "ts":     datetime.utcnow().isoformat(),
            },
            room=data.get("room_id"),
            include_self=False,
        )

    # ── Voice session start ───────────────────────────────────────
    @sio.on("voice_start")
    def sio_voice_start(data):
        sid = request.sid
        if sid in _voice_sessions:
            _voice_sessions[sid]["active"] = False
        sess = {"active": True, "queue": [], "api_key": NOVA_API_KEY}
        _voice_sessions[sid] = sess
        threading.Thread(
            target=_voice_thread, args=(sid, sess), daemon=True
        ).start()
        emit("voice_ready")

    # ── Voice audio chunk from browser ───────────────────────────
    @sio.on("voice_audio")
    def sio_voice_audio(data):
        sid = request.sid
        if sid in _voice_sessions:
            _voice_sessions[sid]["queue"].append(data.get("audio", ""))

    # ── Voice session stop ────────────────────────────────────────
    @sio.on("voice_stop")
    def sio_voice_stop():
        sid = request.sid
        if sid in _voice_sessions:
            _voice_sessions[sid]["active"] = False
            del _voice_sessions[sid]

    # ── Client disconnects ────────────────────────────────────────
    @sio.on("disconnect")
    def sio_disconnect():
        sid = request.sid
        if sid in _voice_sessions:
            _voice_sessions[sid]["active"] = False
            del _voice_sessions[sid]


# ── Voice bridge helpers ──────────────────────────────────────────

def _voice_thread(sid, sess):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_nova_sonic_bridge(sid, sess))
    except Exception as e:
        socketio.emit("voice_error", {"error": str(e)}, room=sid)
    finally:
        loop.close()


async def _nova_sonic_bridge(sid, sess):
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE
    hdrs = {
        "Authorization": f"Bearer {sess['api_key']}",
        "Origin":        "https://api.nova.amazon.com",
    }

    try:
        async with ws_lib.connect(NOVA_WS_URL, ssl=ssl_ctx, additional_headers=hdrs) as ws:
            ev = json.loads(await ws.recv())
            if ev.get("type") != "session.created":
                return

            await ws.send(json.dumps({
                "type":    "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": (
                        "You are MarketMe Voice Agent — a smart, concise AI marketing "
                        "assistant. Help with campaigns, leads, products and business strategy."
                    ),
                    "audio": {
                        "input":  {"turn_detection": {"threshold": 0.5}},
                        "output": {"voice": "matthew"},
                    },
                },
            }))
            await ws.recv()
            socketio.emit("voice_session_active", {}, room=sid)

            async def _send():
                while sess["active"]:
                    if sess["queue"]:
                        await ws.send(json.dumps({
                            "type":  "input_audio_buffer.append",
                            "audio": sess["queue"].pop(0),
                        }))
                    else:
                        await asyncio.sleep(0.04)

            async def _recv():
                while sess["active"]:
                    try:
                        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
                        t  = ev.get("type", "")
                        if t == "response.output_audio.delta":
                            socketio.emit("voice_audio_out", {"audio": ev["delta"]}, room=sid)
                        elif t == "response.output_audio_transcript.done":
                            socketio.emit(
                                "voice_transcript",
                                {"text": ev.get("transcript", "")},
                                room=sid,
                            )
                        elif t == "error":
                            socketio.emit("voice_error", {"error": ev.get("error", {})}, room=sid)
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        break

            await asyncio.gather(_send(), _recv())

    except Exception as e:
        socketio.emit("voice_error", {"error": str(e)}, room=sid)
