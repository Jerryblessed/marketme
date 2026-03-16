from flask import Blueprint, render_template, render_template_string
from models import Business
from config import APP_URL

public_bp = Blueprint("public", __name__)


@public_bp.route("/biz/<slug>")
def biz_page(slug):
    biz = Business.query.filter_by(slug=slug).first_or_404()
    if biz.page_html:
        # Inject live-chat widget before </body>
        widget = f"""<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
<script>(function(){{
var btn=document.createElement('button');btn.innerHTML='💬 Chat with us';
btn.style='position:fixed;bottom:24px;right:24px;background:#f59e0b;color:#000;font-weight:700;padding:12px 24px;border:none;border-radius:8px;cursor:pointer;z-index:9999;box-shadow:0 4px 20px rgba(245,158,11,.4)';
btn.onclick=function(){{window.openLiveChat&&window.openLiveChat();}};document.body.appendChild(btn);
var _s=null;window.openLiveChat=function(){{
var name=prompt('Your name:','');var email=prompt('Your email:','');if(!name||!email)return;
_s=io('{APP_URL}');_s.emit('customer_join',{{slug:'{slug}',name:name,email:email}});
_s.on('chat_ready',function(d){{
var div=document.createElement('div');div.id='mm-chat';
div.style='position:fixed;bottom:90px;right:24px;width:340px;height:480px;background:#0f172a;border-radius:16px;border:1px solid #1e293b;display:flex;flex-direction:column;z-index:9999;box-shadow:0 20px 60px rgba(0,0,0,.5)';
div.innerHTML='<div style="padding:16px;border-bottom:1px solid #1e293b;color:#f1f5f9;font-weight:600;display:flex;justify-content:space-between"><span>Chat · {biz.name}</span><span onclick="document.getElementById(\\\'mm-chat\\\').remove()" style="cursor:pointer;color:#64748b">✕</span></div><div id="mm-msgs" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px"></div><div style="padding:12px;border-top:1px solid #1e293b;display:flex;gap:8px"><input id="mm-inp" placeholder="Type a message..." style="flex:1;background:#1e293b;border:none;border-radius:6px;padding:8px 12px;color:#f1f5f9;font-size:14px;outline:none" onkeydown="if(event.key===\'Enter\')mmSend()"><button onclick="mmSend()" style="background:#f59e0b;border:none;border-radius:6px;padding:8px 14px;font-weight:600;cursor:pointer">Send</button></div>';
document.body.appendChild(div);var rid=d.room_id;
_s.on('live_message',function(m){{var el=document.getElementById('mm-msgs');var mine=m.sender===name;
el.innerHTML+='<div style="background:'+(mine?'#f59e0b22':'#1e293b')+';padding:8px 12px;border-radius:8px;font-size:13px;color:'+(mine?'#fbbf24':'#cbd5e1')+';text-align:'+(mine?'right':'left')+'"><b>'+m.sender+':</b> '+m.text+'</div>';
el.scrollTop=el.scrollHeight;}});
window.mmSend=function(){{var inp=document.getElementById('mm-inp');if(!inp.value.trim())return;
_s.emit('live_message',{{room_id:rid,sender:name,text:inp.value}});
var el=document.getElementById('mm-msgs');el.innerHTML+='<div style="background:#f59e0b22;padding:8px 12px;border-radius:8px;font-size:13px;color:#fbbf24;text-align:right"><b>You:</b> '+inp.value+'</div>';
el.scrollTop=el.scrollHeight;inp.value='';}};}}); }};
}})();</script>"""
        return biz.page_html.replace("</body>", widget + "</body>")

    # Placeholder page
    return render_template_string(
        "<!DOCTYPE html><html><head><title>{{b.name}}</title>"
        "<script src='https://cdn.tailwindcss.com'></script></head>"
        "<body class='bg-slate-900 text-slate-100 min-h-screen flex items-center justify-center'>"
        "<div class='text-center'>"
        "<h1 class='text-5xl font-bold text-amber-400 mb-4'>{{b.name}}</h1>"
        "<p class='text-slate-400 text-xl'>{{b.tagline or 'Coming soon...'}}</p>"
        "</div></body></html>",
        b=biz,
    )


@public_bp.route("/")
@public_bp.route("/dashboard")
@public_bp.route("/miracle")
def serve_spa():
    return render_template("index.html")
