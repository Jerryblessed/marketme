// ── STATE ─────────────────────────────────────────────────────────
const S = {
  token: localStorage.getItem('mm_token'),
  user: JSON.parse(localStorage.getItem('mm_user') || 'null'),
  biz: null, chatHistory: [],
  sessionId: (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)),
  socket: null, voiceActive: false, audioCtx: null, nextPlayTime: 0,
  voiceMediaStream: null, voiceProcessor: null, activeRoom: null, contacts: [],
  pendingImageB64: null, pendingImageMime: 'image/jpeg',
  importMode: 'type', importChips: [], campaignChips: [], _voiceRestartTimer: null
};

function $v(id) { return document.getElementById(id)?.value?.trim() || ''; }
function $el(id) { return document.getElementById(id); }
function escHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function api(method, path, body) {
  const opts = {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(S.token ? { 'Authorization': 'Bearer ' + S.token } : {})
    }
  };
  if (body) opts.body = JSON.stringify(body);
  try {
    const r = await fetch(path, opts);
    return r.json();
  } catch (e) {
    return { error: e.message };
  }
}

let _nt;
function notify(title, body, dur = 5000) {
  $el('notif-title').textContent = title;
  $el('notif-body').textContent = body;
  const n = $el('notification');
  n.style.display = 'block'; n.style.opacity = '1';
  clearTimeout(_nt);
  _nt = setTimeout(() => { n.style.opacity = '0'; setTimeout(() => n.style.display = 'none', 300); }, dur);
}

// ── THEME ─────────────────────────────────────────────────────────
function setTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode);
  localStorage.setItem('mm_theme', mode);
  $el('theme-btn').textContent = mode === 'dark' ? '🌙 Dark' : '☀️ Light';
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme') || 'dark';
  setTheme(cur === 'dark' ? 'light' : 'dark');
}
(function () { const t = localStorage.getItem('mm_theme') || 'dark'; setTheme(t); })();

// ── AUTH ──────────────────────────────────────────────────────────
function authTab(t) {
  document.querySelectorAll('.auth-tab').forEach((el, i) =>
    el.classList.toggle('active', ['login', 'register', 'miracle'][i] === t));
  $el('form-login').style.display = t === 'login' ? '' : 'none';
  $el('form-register').style.display = t === 'register' ? '' : 'none';
  $el('form-miracle').style.display = t === 'miracle' ? '' : 'none';
}
async function doLogin() {
  const r = await api('POST', '/api/auth/login', { email: $v('l-email'), password: $v('l-pass') });
  if (r.error) { $el('l-err').textContent = r.error; return; }
  onAuth(r);
}
async function doRegister() {
  const r = await api('POST', '/api/auth/register', { name: $v('r-name'), email: $v('r-email'), password: $v('r-pass') });
  if (r.error) { $el('r-err').textContent = r.error; return; }
  onAuth(r);
}
async function doMiracle() {
  const r = await api('POST', '/api/auth/miracle/request', { email: $v('m-email') });
  $el('m-msg').textContent = r.message || 'Miracle link sent!';
}
async function checkMiracleToken() {
  const p = new URLSearchParams(window.location.search);
  const t = p.get('miracle'); if (!t) return false;
  const r = await api('POST', '/api/auth/miracle/verify', { token: t });
  if (r.token) { onAuth(r); history.replaceState({}, '', '/'); return true; }
  return false;
}
function onAuth(r) {
  S.token = r.token; S.user = r.user;
  localStorage.setItem('mm_token', r.token);
  localStorage.setItem('mm_user', JSON.stringify(r.user));
  $el('auth').style.display = 'none';
  if (r.user.business_id) loadApp(); else showOnboard();
}
function logout() { localStorage.clear(); location.reload(); }

// ── ONBOARDING ────────────────────────────────────────────────────
let obData = {};
function showOnboard() { $el('auth').style.display = 'none'; $el('onboard').style.display = 'flex'; }
async function obFinish() {
  const name = $v('ob-name');
  if (!name) { $el('ob-err').textContent = 'Business name required'; return; }
  obData = { name, industry: $v('ob-industry'), tagline: $v('ob-tagline'), description: $v('ob-desc') };
  const r = await api('POST', '/api/business', obData);
  if (r.error) { notify('Error', r.error); return; }
  S.biz = r.business; S.user.business_id = r.business.id;
  localStorage.setItem('mm_user', JSON.stringify(S.user));
  $el('onboard').style.display = 'none'; initApp();
}

// ── APP INIT ──────────────────────────────────────────────────────
async function loadApp() {
  $el('auth').style.display = 'none';
  const r = await api('GET', '/api/business');
  if (r.business) { S.biz = r.business; initApp(); } else showOnboard();
}
function initApp() {
  $el('app').style.display = 'flex';
  if (S.biz) {
    $el('sb-biz-name').textContent = S.biz.name;
    populateSettings();
    if (S.biz.page_url)
      $el('page-url-display').innerHTML =
        '<a href="' + S.biz.page_url + '" target="_blank" style="color:var(--accent)">' +
        location.origin + S.biz.page_url + '</a>';
  }
  api('GET', '/api/business').then(r => {
    if (r.business?.smtp_configured)
      $el('smtp-status').textContent = '✓ Email system active';
    else $el('smtp-status').textContent = '⚠ Configure SMTP in server .env';
  });
  initSocket(); loadProducts(); loadContacts(); loadCampaigns(); loadInbox();
  addMsg('assistant',
    'Hello! I\'m your MarketMe Agent powered by Nova 2 Lite.\n\n' +
    'I can:\n• Navigate the app — try "show me contacts" or "go to campaigns"\n' +
    '• Analyse images — click 📎 and upload one\n' +
    '• Find leads, add products, launch campaigns\n' +
    '• Switch themes — try "dark mode" or "light mode"\n' +
    '• Open any form — try "add a new product"\n\nWhat shall we do today?',
    null, null);
}

// ── SOCKET ────────────────────────────────────────────────────────
function initSocket() {
  S.socket = io();
  if (S.biz) S.socket.emit('join_biz', { business_id: S.biz.id });
  S.socket.on('inbox_update', d => {
    $el('inbox-dot').style.display = 'block';
    notify('📥 ' + d.intent.toUpperCase(), d.from + ' (AI replied: ' + (d.ai_replied ? 'yes' : 'no') + ')');
    loadInbox();
  });
  S.socket.on('campaign_update', d => { notify('📣 Campaign Sent', d.name + ' — ' + d.sent + ' delivered'); loadCampaigns(); });
  S.socket.on('leads_found', d => { notify('🔍 Leads Found', d.count + ' contacts added'); loadContacts(); });
  S.socket.on('new_chat_request', d => { notify('⚡ New Chat', d.customer_name + ' wants to chat'); addChatRoom(d); });
  S.socket.on('voice_ready', () => updateVoiceStatus('Session starting...'));
  S.socket.on('voice_session_active', () => updateVoiceStatus('🟢 Listening — speak now'));
  S.socket.on('voice_audio_out', d => playAudioChunk(d.audio));
  S.socket.on('voice_transcript', d => {
    const tb = $el('transcript-box');
    tb.textContent += '\nAgent: ' + d.text;
    tb.scrollTop = tb.scrollHeight;
  });
  S.socket.on('voice_error', d => {
    updateVoiceStatus('Error: ' + (typeof d.error === 'object' ? JSON.stringify(d.error) : d.error));
    stopVoice();
  });
  S.socket.on('live_message', d => renderLiveMsg(d));
  S.socket.on('owner_joined', d => { $el('live-chat-header').textContent = 'Chat — Room ' + d.room_id; });
}

// ── NAVIGATION ────────────────────────────────────────────────────
function showPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  $el('panel-' + name).classList.add('active');
  const el = document.querySelector('[data-panel="' + name + '"]');
  if (el) el.classList.add('active');
  if (name === 'inbox') $el('inbox-dot').style.display = 'none';
}

// ── CHAT ──────────────────────────────────────────────────────────
function handleImgUpload(input) {
  const file = input.files[0]; if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    const data = e.target.result;
    const b64 = data.split(',')[1];
    S.pendingImageB64 = b64; S.pendingImageMime = file.type || 'image/jpeg';
    const wrap = $el('img-preview-wrap');
    wrap.style.display = 'block';
    wrap.innerHTML = '<img class="img-preview" src="' + data + '" title="Click to remove" onclick="clearImg()">';
    notify('📎 Image ready', 'Will be sent with your next message');
  };
  reader.readAsDataURL(file);
}
function clearImg() {
  S.pendingImageB64 = null;
  $el('img-preview-wrap').style.display = 'none';
  $el('img-preview-wrap').innerHTML = '';
  $el('img-upload').value = '';
}

async function sendChat() {
  const inp = $el('chat-inp'); const txt = inp.value.trim();
  if (!txt && !S.pendingImageB64) return;
  inp.value = '';
  const imgB64 = S.pendingImageB64; const imgMime = S.pendingImageMime;
  if (imgB64) clearImg();

  const userContent = imgB64 ? '[Image] ' + (txt || 'Analyse this image') : txt;
  addMsg('user', userContent, null, null, imgB64 ? ('data:' + imgMime + ';base64,' + imgB64) : null);
  S.chatHistory.push({ role: 'user', content: txt || 'Analyse this image' });

  const typId = 'typ-' + Date.now();
  $el('chat-area').insertAdjacentHTML('beforeend',
    '<div id="' + typId + '" class="msg assistant"><div class="msg-bubble">' +
    '<div class="typing-dot"><span></span><span></span><span></span></div></div></div>');
  scrollChat();

  const body = { messages: S.chatHistory, session_id: S.sessionId };
  if (imgB64) { body.image_b64 = imgB64; body.image_mime = imgMime; }

  const r = await api('POST', '/api/chat', body);
  $el(typId)?.remove();
  addMsg('assistant', r.content || 'Sorry, empty response.', r.intent, r.action, null);
  if (r.content) S.chatHistory.push({ role: 'assistant', content: r.content });
  if (S.chatHistory.length > 40) S.chatHistory = S.chatHistory.slice(-40);

  if (r.action) {
    const t = r.action.type || r.intent;
    if (t === 'navigate') { showPanel(r.action.panel); }
    if (t === 'open_modal') { showModal('modal-' + r.action.modal); }
    if (t === 'toggle_theme') { setTheme(r.action.mode || 'dark'); }
    if (t === 'show_notification') { notify(r.action.title || '', r.action.message || ''); }
    if (t === 'product_added') { loadProducts(); notify('✅ Product Added', r.action.name || ''); }
    if (t === 'campaign_drafted') { loadCampaigns(); notify('📣 Campaign Drafted', r.action.name || ''); }
    if (t === 'lead_search_started') notify('🔍 Lead Search Started', 'Running in background');
    if (t === 'followup_scheduled') notify('⏰ Follow-up Scheduled', 'Sending in ' + r.action.delay_hours + 'h');
    if (t === 'page_generated') {
      if (S.biz) S.biz.page_url = r.action.url;
      $el('page-url-display').innerHTML =
        '<a href="' + r.action.url + '" target="_blank" style="color:var(--accent)">' +
        location.origin + r.action.url + '</a>';
      notify('🌐 Page Live!', location.origin + r.action.url);
    }
  }
}

function addMsg(role, content, intent, action, imgSrc) {
  const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  let ih = intent ? '<div class="intent-badge">⚡ ' + escHtml(intent) + '</div>' : '';
  let imgHtml = imgSrc ? '<img class="msg-img" src="' + escHtml(imgSrc) + '">' : '';
  let ah = '';
  if (action && (action.type || action.panel)) {
    const t = action.type || intent;
    const msgs = {
      navigate: '🗂 Navigating to ' + escHtml(action.panel || ''),
      open_modal: '📋 Opening ' + escHtml(action.modal || '') + ' form',
      toggle_theme: '🎨 Theme switched to ' + escHtml(action.mode || ''),
      product_added: '✅ Product "' + escHtml(action.name || '') + '" added',
      campaign_drafted: '📣 Campaign drafted — review in Campaigns',
      lead_search_started: '🔍 Searching for leads in background...',
      followup_scheduled: '⏰ Follow-up scheduled',
      page_generated: '🌐 Page: <a href="' + escHtml(action.url || '') + '" target="_blank" style="color:var(--accent)">View</a>',
      live_chat: '⚡ Chat room: ' + (action.room_id || 'none'),
    };
    if (msgs[t]) ah = '<div class="action-card">' + msgs[t] + '</div>';
  }
  $el('chat-area').insertAdjacentHTML('beforeend',
    '<div class="msg ' + role + '">' + imgHtml +
    '<div class="msg-bubble">' + escHtml(content) + '</div>' +
    ih + ah + '<div class="msg-time">' + time + '</div></div>');
  scrollChat();
}
function scrollChat() { const a = $el('chat-area'); a.scrollTop = a.scrollHeight; }
function clearChat() {
  $el('chat-area').innerHTML = ''; S.chatHistory = [];
  S.sessionId = (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2));
  addMsg('assistant', 'Chat cleared. How can I help?', null, null, null);
}

// ── VOICE ─────────────────────────────────────────────────────────
function toggleVoiceInChat() { S.voiceActive ? stopVoice() : startVoice(); $el('voice-toggle-btn').classList.toggle('active', !S.voiceActive); }
function toggleVoice() { S.voiceActive ? stopVoice() : startVoice(); }
function startVoice() {
  if (S.voiceActive) return;
  navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
    S.voiceActive = true; S.voiceMediaStream = stream;
    S.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    S.audioCtx.resume(); S.nextPlayTime = 0;
    const src = S.audioCtx.createMediaStreamSource(stream);
    const proc = S.audioCtx.createScriptProcessor(2048, 1, 1);
    proc.onaudioprocess = e => {
      if (!S.voiceActive) return;
      const raw = e.inputBuffer.getChannelData(0);
      const ratio = S.audioCtx.sampleRate / 24000;
      const out = new Int16Array(Math.floor(raw.length / ratio));
      for (let i = 0; i < out.length; i++) {
        const s = Math.max(-1, Math.min(1, raw[Math.floor(i * ratio)]));
        out[i] = s < 0 ? s * 32768 : s * 32767;
      }
      S.socket.emit('voice_audio', { audio: btoa(String.fromCharCode(...new Uint8Array(out.buffer))) });
    };
    src.connect(proc); proc.connect(S.audioCtx.destination); S.voiceProcessor = proc;
    $el('mic-btn').classList.add('recording');
    $el('transcript-box').textContent = 'Session started...\n';
    updateVoiceStatus('Connecting to Nova Sonic...');
    S.socket.emit('voice_start', { business_id: S.biz?.id });
    S._voiceRestartTimer = setTimeout(() => {
      if (S.voiceActive) { updateVoiceStatus('Refreshing session...'); stopVoice(); setTimeout(startVoice, 1200); }
    }, 7 * 60 * 1000);
  }).catch(e => notify('Microphone Error', e.message));
}
function stopVoice() {
  clearTimeout(S._voiceRestartTimer); S.voiceActive = false;
  if (S.voiceMediaStream) { S.voiceMediaStream.getTracks().forEach(t => t.stop()); S.voiceMediaStream = null; }
  if (S.voiceProcessor) { S.voiceProcessor.disconnect(); S.voiceProcessor = null; }
  if (S.audioCtx) { S.audioCtx.close(); S.audioCtx = null; }
  S.socket.emit('voice_stop');
  $el('mic-btn').classList.remove('recording'); $el('voice-toggle-btn').classList.remove('active');
  updateVoiceStatus('Session ended. Click to start again.');
}
function updateVoiceStatus(msg) { $el('voice-status').textContent = msg; }
function playAudioChunk(b64) {
  if (!S.audioCtx) return;
  try {
    const bin = atob(b64), buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    const i16 = new Int16Array(buf.buffer);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
    const ab = S.audioCtx.createBuffer(1, f32.length, 24000);
    ab.getChannelData(0).set(f32);
    const src = S.audioCtx.createBufferSource();
    src.buffer = ab; src.connect(S.audioCtx.destination);
    const now = S.audioCtx.currentTime;
    if (S.nextPlayTime < now) S.nextPlayTime = now;
    src.start(S.nextPlayTime); S.nextPlayTime += ab.duration;
  } catch (e) { }
}

// ── PRODUCTS ──────────────────────────────────────────────────────
async function loadProducts() {
  const r = await api('GET', '/api/products');
  const g = $el('products-grid');
  if (!r.products || !r.products.length) {
    g.innerHTML = '<div style="color:var(--muted);font-family:\'DM Mono\',monospace;font-size:13px;grid-column:1/-1;padding:24px 0">No products yet — add one or ask the agent!</div>';
    return;
  }
  g.innerHTML = r.products.map(p => `<div class="product-card">
    ${p.image_url ? '<img src="' + escHtml(p.image_url) + '" style="width:100%;height:120px;object-fit:cover;border-radius:8px;margin-bottom:12px" onerror="this.style.display=\'none\'">' : ''}
    <div class="product-name">${escHtml(p.name)}</div>
    <div class="product-price">${escHtml(p.currency)} ${Number(p.price).toFixed(2)}</div>
    <div class="product-desc">${escHtml(p.description || '')}</div>
    ${p.category ? '<div class="product-category">' + escHtml(p.category) + '</div>' : ''}
    <button class="btn-sm btn-danger" style="margin-top:12px" onclick="delProduct(${p.id})">Delete</button>
  </div>`).join('');
}
async function saveProduct() {
  const r = await api('POST', '/api/products', {
    name: $v('p-name'), description: $v('p-desc'),
    price: parseFloat($v('p-price') || 0), currency: $v('p-currency') || 'USD',
    category: $v('p-category'), image_url: $v('p-img')
  });
  if (r.product) { hideModal('modal-add-product'); loadProducts(); notify('✅ Product Added', r.product.name); }
  else if (r.error) notify('Error', r.error);
}
async function delProduct(id) { if (confirm('Delete?')) { await api('DELETE', '/api/products/' + id); loadProducts(); } }

// ── CONTACTS ──────────────────────────────────────────────────────
async function loadContacts() {
  const r = await api('GET', '/api/contacts'); S.contacts = r.contacts || [];
  $el('contacts-count').textContent = (S.contacts.length || 0) + ' total contacts';
  const tb = $el('contacts-table');
  if (!S.contacts.length) {
    tb.innerHTML = '<tr><td colspan="6" style="color:var(--muted);font-family:\'DM Mono\',monospace;text-align:center;padding:24px">No contacts yet</td></tr>';
    return;
  }
  tb.innerHTML = S.contacts.map(c => `<tr>
    <td style="font-weight:600">${escHtml(c.name || '—')}</td>
    <td style="font-family:'DM Mono',monospace;font-size:12px">${escHtml(c.email)}</td>
    <td style="color:var(--muted)">${escHtml(c.company || '—')}</td>
    <td><span class="status-badge status-${escHtml(c.status)}">${escHtml(c.status)}</span></td>
    <td><span class="status-badge status-${escHtml(c.source)}">${escHtml(c.source)}</span></td>
    <td><button class="btn-sm btn-danger" onclick="delContact(${c.id})">✕</button></td>
  </tr>`).join('');
}
async function saveContact() {
  const r = await api('POST', '/api/contacts', {
    name: $v('c-name'), email: $v('c-email'),
    company: $v('c-company'), phone: $v('c-phone'), notes: $v('c-notes')
  });
  if (r.contact) { hideModal('modal-add-contact'); loadContacts(); }
  else if (r.error) notify('Error', r.error);
}
async function delContact(id) { if (confirm('Delete?')) { await api('DELETE', '/api/contacts/' + id); loadContacts(); } }

// ── IMPORT CONTACTS ───────────────────────────────────────────────
function switchImportTab(tab) {
  S.importMode = tab;
  ['type', 'csv', 'pool'].forEach(t => {
    $el('import-' + t + '-panel').style.display = t === tab ? '' : 'none';
    $el('import-tab-' + t).className = 'btn-sm ' + (t === tab ? 'btn-accent' : 'btn-outline');
  });
  if (tab === 'pool') loadPoolContacts();
}
async function loadPoolContacts() {
  const r = await api('GET', '/api/contacts/csv-pool');
  const list = $el('pool-list');
  if (!r.contacts || !r.contacts.length) { list.innerHTML = '<div style="color:var(--muted);font-size:13px">No shared contacts yet</div>'; return; }
  list.innerHTML = r.contacts.map(c => `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)">
    <div><div style="font-size:13px;font-weight:600">${escHtml(c.name || c.email)}</div>
    <div style="font-size:11px;color:var(--muted);font-family:'DM Mono',monospace">${escHtml(c.email)} ${c.company ? '· ' + escHtml(c.company) : ''}</div></div>
    <input type="checkbox" value="${escHtml(c.email)}" class="pool-check" style="width:16px;height:16px">
  </div>`).join('');
}

function handleChipKey(e) {
  if (e.key === 'Enter' || e.key === ',' || e.key === ';') { e.preventDefault(); addChip($el('chip-input').value, 'import'); }
}
function handleChipInput(e) {
  const val = e.target.value;
  if (val.includes(',')) { val.split(',').forEach(v => addChip(v, 'import')); e.target.value = ''; }
}
function handleCampaignChipKey(e) {
  if (e.key === 'Enter' || e.key === ',' || e.key === ';') { e.preventDefault(); addChip($el('campaign-chip-input').value, 'campaign'); }
}
function handleCampaignChipInput(e) {
  const val = e.target.value;
  if (val.includes(',')) { val.split(',').forEach(v => addChip(v, 'campaign')); e.target.value = ''; }
}
function addChip(raw, target) {
  const email = raw.trim().replace(/[,;]/g, '');
  if (!email || !email.includes('@')) return;
  if (target === 'import') {
    if (S.importChips.includes(email)) return;
    S.importChips.push(email);
    renderChips('email-chips-box', S.importChips, 'import');
    $el('chip-input').value = '';
    $el('chip-count').textContent = S.importChips.length;
  } else {
    if (S.campaignChips.includes(email)) return;
    S.campaignChips.push(email);
    renderChips('campaign-chips-box', S.campaignChips, 'campaign');
    $el('campaign-chip-input').value = '';
    $el('campaign-chip-count').textContent = S.campaignChips.length + ' extra emails';
  }
}
function removeChip(email, target) {
  if (target === 'import') {
    S.importChips = S.importChips.filter(e => e !== email);
    renderChips('email-chips-box', S.importChips, 'import');
    $el('chip-count').textContent = S.importChips.length;
  } else {
    S.campaignChips = S.campaignChips.filter(e => e !== email);
    renderChips('campaign-chips-box', S.campaignChips, 'campaign');
    $el('campaign-chip-count').textContent = S.campaignChips.length + ' extra emails';
  }
}
function renderChips(boxId, chips, target) {
  const box = $el(boxId); const inp = box.querySelector('input');
  box.querySelectorAll('.email-chip').forEach(c => c.remove());
  chips.forEach(email => {
    const chip = document.createElement('div'); chip.className = 'email-chip';
    chip.innerHTML = escHtml(email) + '<span class="chip-x" onclick="removeChip(\'' + escHtml(email) + '\',\'' + target + '\')">✕</span>';
    box.insertBefore(chip, inp);
  });
}
function handleCsvFile(input) {
  const file = input.files[0]; if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    const lines = e.target.result.split('\n'); const emails = [];
    lines.forEach(line => { const match = line.match(/[\w.+\-]+@[\w\-]+\.\w{2,6}/); if (match) emails.push(match[0]); });
    $el('csv-preview').textContent = `Found ${emails.length} emails in CSV`;
    emails.forEach(em => addChip(em, 'import'));
  };
  reader.readAsText(file);
}
function handleCsvDrop(e) {
  e.preventDefault(); $el('csv-drop-zone').classList.remove('dragover');
  const file = e.dataTransfer.files[0]; if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {
    const emails = [];
    ev.target.result.split('\n').forEach(line => { const m = line.match(/[\w.+\-]+@[\w\-]+\.\w{2,6}/); if (m) emails.push(m[0]); });
    $el('csv-preview').textContent = `Found ${emails.length} emails`;
    emails.forEach(em => addChip(em, 'import'));
  };
  reader.readAsText(file);
}
function handleCampaignCsv(input) {
  const file = input.files[0]; if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    e.target.result.split('\n').forEach(line => { const m = line.match(/[\w.+\-]+@[\w\-]+\.\w{2,6}/); if (m) addChip(m[0], 'campaign'); });
  };
  reader.readAsText(file);
}
async function doImport() {
  let emails = [];
  if (S.importMode === 'type' || S.importMode === 'csv') {
    emails = S.importChips.map(e => ({ email: e }));
  } else if (S.importMode === 'pool') {
    document.querySelectorAll('.pool-check:checked').forEach(cb => emails.push({ email: cb.value }));
  }
  if (!emails.length) { notify('Nothing to import', 'Add some emails first'); return; }
  const r = await api('POST', '/api/contacts/import', { emails });
  if (r.ok) {
    hideModal('modal-import-contacts'); loadContacts();
    notify('✅ Imported', r.added + ' new contacts added');
    S.importChips = []; renderChips('email-chips-box', [], 'import');
  } else notify('Error', r.error || 'Import failed');
}
async function startLeadSearch() {
  await api('POST', '/api/chat', {
    messages: [{ role: 'user', content: 'Find business leads in ' + $v('fl-industry') + ' based in ' + $v('fl-location') + ' related to ' + $v('fl-keywords') }],
    session_id: S.sessionId
  });
  hideModal('modal-find-leads');
  notify('🔍 Lead Search Running', 'Using Playwright + shared pool fallback');
}

// ── CAMPAIGNS ─────────────────────────────────────────────────────
async function loadCampaigns() {
  const r = await api('GET', '/api/campaigns'); const tb = $el('campaigns-table');
  if (!r.campaigns || !r.campaigns.length) {
    tb.innerHTML = '<tr><td colspan="6" style="color:var(--muted);text-align:center;padding:24px;font-family:\'DM Mono\',monospace">No campaigns yet</td></tr>';
    return;
  }
  tb.innerHTML = r.campaigns.map(c => `<tr>
    <td style="font-weight:600">${escHtml(c.name || '—')}</td>
    <td style="font-family:'DM Mono',monospace;font-size:12px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(c.subject || '—')}</td>
    <td><span class="status-badge status-${escHtml(c.status)}">${escHtml(c.status)}</span></td>
    <td style="font-family:'DM Mono',monospace">${c.sent_count || 0}</td>
    <td style="font-family:'DM Mono',monospace;font-size:12px;color:var(--muted)">${c.scheduled_at ? new Date(c.scheduled_at).toLocaleString() : '—'}</td>
    <td>${c.status === 'draft' ? '<button class="btn-sm btn-accent" onclick="sendCampaignNow(' + c.id + ')">Send</button>' : ''}</td>
  </tr>`).join('');
}
async function saveCampaign(sendNow) {
  await loadContacts();
  const audience = $v('cp-audience');
  let ids = [];
  if (audience === 'all') ids = S.contacts.map(c => c.id);
  else if (audience === 'new') ids = S.contacts.filter(c => c.status === 'new').map(c => c.id);
  else if (audience === 'interested') ids = S.contacts.filter(c => c.status === 'interested').map(c => c.id);
  const sched = $v('cp-schedule');
  const r = await api('POST', '/api/campaigns', {
    name: $v('cp-name'), subject: $v('cp-subject'),
    body_plain: $v('cp-body'), contact_ids: ids, raw_emails: S.campaignChips,
    send_now: sendNow, scheduled_at: (!sendNow && sched) ? sched : null
  });
  if (r.campaign) {
    hideModal('modal-add-campaign'); loadCampaigns();
    S.campaignChips = []; renderChips('campaign-chips-box', [], 'campaign');
    notify(sendNow ? '📣 Campaign Queued' : '📋 Draft Saved', r.campaign.name);
  } else if (r.error) notify('Error', r.error);
}
async function sendCampaignNow(id) {
  await api('POST', '/api/campaigns/' + id + '/send');
  loadCampaigns(); notify('📣 Queued', 'Sending shortly');
}

// ── INBOX ─────────────────────────────────────────────────────────
async function loadInbox() {
  const r = await api('GET', '/api/email-threads'); const tb = $el('inbox-table');
  if (!r.threads || !r.threads.length) {
    tb.innerHTML = '<tr><td colspan="6" style="color:var(--muted);font-family:\'DM Mono\',monospace;text-align:center;padding:24px">No threads yet — agent monitors inbox every 2 min</td></tr>';
    return;
  }
  tb.innerHTML = r.threads.map(t => `<tr>
    <td style="font-family:'DM Mono',monospace;font-size:12px">${escHtml(t.from_email)}</td>
    <td style="font-weight:600;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(t.subject || '—')}</td>
    <td><span class="status-badge intent-${escHtml(t.intent)}">${escHtml(t.intent)}</span></td>
    <td>${t.ai_auto_reply ? '<span style="color:var(--green);font-family:\'DM Mono\',monospace;font-size:11px">✓ Replied</span>' : '<span style="color:var(--muted);font-family:\'DM Mono\',monospace;font-size:11px">—</span>'}</td>
    <td style="font-family:'DM Mono',monospace;font-size:12px;color:var(--muted)">${new Date(t.received_at).toLocaleString()}</td>
    <td style="font-size:12px;color:var(--muted);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml((t.body_snippet || '').slice(0, 70))}</td>
  </tr>`).join('');
}

// ── LIVE CHATS ────────────────────────────────────────────────────
function addChatRoom(d) {
  const list = $el('chat-rooms-list');
  if (list.querySelector('div[style*="color"]')) list.innerHTML = '';
  const item = document.createElement('div'); item.className = 'room-item'; item.id = 'room-' + d.room_id;
  item.innerHTML = '<div class="room-name">' + escHtml(d.customer_name) + '</div><div class="room-status">' + escHtml(d.customer_email || '') + '</div>';
  item.onclick = () => joinRoom(d.room_id, d.customer_name);
  list.prepend(item);
}
function joinRoom(roomId, name) {
  S.activeRoom = roomId;
  document.querySelectorAll('.room-item').forEach(r => r.classList.remove('active-room'));
  const item = $el('room-' + roomId); if (item) item.classList.add('active-room');
  $el('live-chat-header').textContent = 'Chat with ' + name + ' (connecting...)';
  $el('live-chat-msgs').innerHTML = '';
  S.socket.emit('owner_join_chat', { room_id: roomId });
}
function renderLiveMsg(d) {
  const el = $el('live-chat-msgs'); if (!el) return;
  const isOwner = d.sender === (S.user?.name || 'Owner');
  el.insertAdjacentHTML('beforeend',
    '<div style="background:' + (isOwner ? '#f59e0b1a' : 'var(--card)') +
    ';border:1px solid ' + (isOwner ? '#f59e0b44' : 'var(--border)') +
    ';border-radius:10px;padding:10px 14px;font-size:13px;align-self:' + (isOwner ? 'flex-end' : 'flex-start') +
    ';max-width:80%"><b style="font-size:11px;color:var(--muted)">' + escHtml(d.sender) + '</b><br>' + escHtml(d.text) + '</div>');
  el.scrollTop = el.scrollHeight;
}
function sendLiveReply() {
  if (!S.activeRoom) return;
  const inp = $el('live-reply-inp'); if (!inp.value.trim()) return;
  const sender = S.user?.name || 'Owner';
  S.socket.emit('live_message', { room_id: S.activeRoom, sender: sender, text: inp.value });
  renderLiveMsg({ sender: sender, text: inp.value });
  inp.value = '';
}

// ── SETTINGS ──────────────────────────────────────────────────────
function populateSettings() {
  if (!S.biz) return;
  $el('s-biz-name').value = S.biz.name || '';
  $el('s-biz-industry').value = S.biz.industry || '';
  $el('s-biz-tagline').value = S.biz.tagline || '';
  $el('s-biz-desc').value = S.biz.description || '';
}
async function saveBizInfo() {
  const r = await api('PUT', '/api/business/settings', {
    name: $v('s-biz-name'), industry: $v('s-biz-industry'),
    tagline: $v('s-biz-tagline'), description: $v('s-biz-desc')
  });
  if (r.ok) { S.biz = r.business; $el('sb-biz-name').textContent = S.biz.name; notify('✅ Saved', 'Business info updated'); }
  else notify('Error', r.error || 'Save failed');
}
async function generatePage() {
  notify('⏳ Generating', 'AI building your page...', 10000);
  const r = await api('POST', '/api/business/generate-page');
  if (r.ok) {
    if (S.biz) S.biz.page_url = r.url;
    $el('page-url-display').innerHTML = '<a href="' + r.url + '" target="_blank" style="color:var(--accent)">' + location.origin + r.url + '</a>';
    notify('🌐 Page Live!', location.origin + r.url);
  } else notify('Error', r.error || 'Failed');
}

// ── MODALS ────────────────────────────────────────────────────────
function showModal(id) {
  $el(id).style.display = 'flex';
  if (id === 'modal-import-contacts') {
    S.importChips = []; renderChips('email-chips-box', [], 'import');
    $el('chip-count').textContent = '0';
    switchImportTab('type');
  }
  if (id === 'modal-add-campaign') {
    S.campaignChips = []; renderChips('campaign-chips-box', [], 'campaign');
    $el('campaign-chip-count').textContent = '0 extra emails';
  }
}
function hideModal(id) { $el(id).style.display = 'none'; }
function closeModalOutside(e, id) { if (e.target.id === id) hideModal(id); }

// ── BOOT ──────────────────────────────────────────────────────────
(async function boot() {
  if (await checkMiracleToken()) return;
  if (S.token && S.user) loadApp(); else $el('auth').style.display = 'flex';
})();
