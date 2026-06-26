// ═══ TRANSLATE HELPER ════════════════════════════════════════════════════════
function t(key, ...args) {
  const L = (typeof LANG!=='undefined' && typeof curLang!=='undefined' && LANG[curLang]) ? LANG[curLang] : {};
  let s = L[key] !== undefined ? L[key] : key;
  args.forEach((a, i) => { s = s.replace('{'+i+'}', a); });
  return s;
}

// ═══ FETCH HELPER (session-based, CSRF-protected) ════════════════════════════
const _csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

function apiFetch(url, opts) {
  const options = Object.assign({credentials: 'same-origin'}, opts || {});
  const method = (options.method || 'GET').toUpperCase();
  if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
    options.headers = Object.assign({'X-CSRF-Token': _csrfToken}, options.headers || {});
  }
  return fetch(url, options)
    .then(r => {
      if (r.status === 401) { window.location.href = '/login'; throw new Error('Unauthorized'); }
      return r;
    });
}

// ═══ STATE ════════════════════════════════════════════════════════════════
let live = null, spPts = [], currentZip = null;
let mapImg = null, _mapLoading = false;
let _lapBases = {};
const COLORS = ['#e8150c','#3498db','#27ae60','#f39c12','#9b59b6','#e67e22','#1abc9c','#e91e63'];

function autoRestart() { return document.getElementById('auto-restart').checked; }

// Tab-System entfernt — Navigation läuft jetzt über navTo() / Sidebar

// ═══ LANGUAGE SYSTEM ════════════════════════════════════════════════════

let curLang = localStorage.getItem('acLang') || 'de';

function applyLang() {
  const L = LANG[curLang] || LANG.de;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (L[key] !== undefined) el.textContent = L[key];
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    if (L[key] !== undefined) el.placeholder = L[key];
  });
  // Support for <option> elements with data-i18n
  document.querySelectorAll('option[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (L[key] !== undefined) el.textContent = L[key];
  });
  document.documentElement.lang = curLang;
  const btn = document.getElementById('lang-toggle');
  if (btn) btn.textContent = curLang === 'de' ? 'EN' : 'DE';
}

function toggleLang() {
  curLang = curLang === 'de' ? 'en' : 'de';
  localStorage.setItem('acLang', curLang);
  applyLang();
}

// ═══ TOAST ════════════════════════════════════════════════════════════════
function toast(msg, type="ok") {
  const el = document.getElementById("toast");
  el.textContent = msg; el.className = "toast " + type;
  el.style.display = "block";
  clearTimeout(el._t); el._t = setTimeout(() => el.style.display = "none", 3500);
}

// ═══ SERVER CONTROL ═══════════════════════════════════════════════════════
let _ctrlLocked = false;
function ctrl(action) {
  if (_ctrlLocked) { toast("Bitte warten…","info"); return; }
  _ctrlLocked = true;
  apiFetch("/control/" + action, {method:"POST"})
    .then(r=>r.json())
    .then(d => { toast(d.ok ? "✓ "+action : "✗ "+d.msg, d.ok?"ok":"err"); setTimeout(refreshLive,1500); })
    .finally(() => setTimeout(() => { _ctrlLocked = false; }, 8000));
}

// ═══ LIVE DATA ════════════════════════════════════════════════════════════
function refreshLive() {
  apiFetch("/api/live")
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(d => {
      live = d;
      const now = Date.now();
      const activeIds = new Set();
      (d.drivers || []).forEach(drv => {
        activeIds.add(String(drv.id));
        if (drv.lapTime > 0) {
          _lapBases[drv.id] = { base: drv.lapTime, t: now, lapCount: drv.lapCount };
        } else {
          delete _lapBases[drv.id];
        }
      });
      for (const id of Object.keys(_lapBases)) {
        if (!activeIds.has(id)) delete _lapBases[id];
      }
      if (d.spline_points?.length > 10) spPts = d.spline_points;
      _detectLiveEvents(d.drivers);
      updateHeader(d); updateDash(d); updateDrivers(d); updateLaps(d);
      _renderChat(d.chat||[], "dash-chat-box");
      _renderChat(d.chat||[], "chat-box");
      drawMap(d);
    }).catch(e => { if (e !== 'Unauthorized') console.warn("refreshLive:", e); });
}

function updateHeader(d) {
  const active = d.status === "active";
  document.getElementById("h-dot").className = "dot " + (active?"on":d.status==="failed"?"fail":"");
  document.getElementById("h-status").textContent = d.status;
  document.getElementById("h-players").textContent = d.info?.clients ?? d.drivers.length;
  if (d.info) {
    document.getElementById("h-maxp").textContent = d.info.maxclients || "?";
    document.getElementById("h-track").textContent = d.info.track || "";
  }
}

function updateDash(d) {
  const active = d.status === "active";
  const badge = document.getElementById("d-badge");
  badge.textContent = active ? "Online" : d.status;
  badge.className = "bdg " + (active?"bdg-on":d.status==="failed"?"bdg-err":"bdg-off");
  document.getElementById("d-up").textContent = active ? "Server läuft" : "Server offline";
  document.getElementById("d-cl").textContent = d.info?.clients ?? d.drivers.length;
  document.getElementById("d-mcl").textContent = d.info?.maxclients || "?";
  const cpu = d.system?.cpu ?? 0, ram = d.system?.mem_percent ?? 0;
  document.getElementById("d-cpu").textContent = cpu.toFixed(1);
  document.getElementById("cpu-bar").style.width = cpu + "%";
  document.getElementById("cpu-bar").className = "bar-fill bar-cpu" + (cpu>80?" bar-hot":"");
  document.getElementById("d-ram").textContent = ram.toFixed(1);
  document.getElementById("ram-bar").style.width = ram + "%";
  const used = d.system?.mem_used_mb??0, total = d.system?.mem_total_mb??0;
  document.getElementById("d-ram-det").textContent = total ? used+" / "+total+" MB" : "";
  if (d.info) {
    document.getElementById("i-name").textContent = (d.info.name||"").replace(/\s*ℹ\d+$/, "") || "—";
    document.getElementById("i-track").textContent = d.info.track || "—";
    document.getElementById("i-layout").textContent = d.info.trackconfig || "—";
    const ip = d.info.ip || "";
    document.getElementById("pub-ip").textContent = ip || "—";
    const hp = d.info.cport || 8081;
    const inv = document.getElementById("inv-link");
    if (ip) inv.href = `https://acstuff.ru/s/q:race/online/join?ip=${ip}&httpPort=${hp}`;
    document.getElementById("i-sess").textContent = (["Practice","Qualify","Race"])[d.info.session] || "Practice";
  }
  renderCards("d-drivers", d.drivers, false);
}

function updateDrivers(d) { renderCards("f-drivers", d.drivers, true); }

function renderCards(id, drivers, showAct) {
  const el = document.getElementById(id);
  if (!drivers?.length) {
    el.innerHTML = `<div class="empty"><div class="empty-ico">🏁</div><div>${t('no_drivers')}</div></div>`;
    return;
  }
  el.innerHTML = drivers.map((d,i) => `
    <div class="dc">
      <div class="dc-head">
        <div><div class="dc-name" title="${esc(d.name)}">${esc(d.name)}</div><div class="dc-car">${esc(d.model)} · #${d.id}</div></div>
        <div class="dc-laps">Rnd ${d.lapCount||0}</div>
      </div>
      <div class="sp-bar"><div class="sp-fill" style="width:${((d.spLine||0)*100).toFixed(1)}%"></div></div>
      <div class="sp-txt">${((d.spLine||0)*100).toFixed(1)}% Strecke</div>
      <div class="dc-stats">
        <div class="dc-st"><div class="dc-stv best-t">${fmt(d.bestLap)}</div><div class="dc-stl">Beste</div></div>
        <div class="dc-st"><div class="dc-stv">${fmt(d.lastLap)}</div><div class="dc-stl">Letzte</div></div>
        <div class="dc-st"><div class="dc-stv" id="ct-${d.id}">${fmt(d.lapTime)}</div><div class="dc-stl">Aktuell</div></div>
      </div>
      ${showAct ? `<div class="dc-acts">
        <button class="btn btn-danger btn-sm" onclick="kick(${d.id},'${esc(d.name)}')">⊘ Kick</button>
        <button class="btn btn-warn btn-sm" onclick="ban(${d.id},'${esc(d.guid)}','${esc(d.name)}')">⛔ Ban</button>
      </div>` : ""}
    </div>`).join("");
}

function updateLaps(d) {
  const tb = document.getElementById("lap-tbody");
  if (!d.drivers?.length) { tb.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:48px">Keine Daten</td></tr>`; return; }
  const sorted = [...d.drivers].sort((a,b) => a.bestLap&&b.bestLap?a.bestLap-b.bestLap:a.bestLap?-1:b.bestLap?1:b.lapCount-a.lapCount);
  tb.innerHTML = sorted.map((r,i) => {
    const pc = ["pos-1","pos-2","pos-3"][i]||"";
    return `<tr><td class="${pc}">${["🥇","🥈","🥉"][i]||"P"+(i+1)}</td><td><strong>${esc(r.name)}</strong></td><td style="color:var(--muted)">${esc(r.model)}</td><td>${r.lapCount||0}</td><td class="${i===0&&r.bestLap?"best-t":""}">${fmt(r.bestLap)}</td><td>${fmt(r.lastLap)}</td><td style="color:var(--muted)" id="clt-${r.id}">${fmt(r.lapTime)}</td></tr>`;
  }).join("");
}

// ═══ EVENTS ═══════════════════════════════════════════════════════════════
let _allEvents = [];
let _prevSnapshot = null;

function _evRow(e, compact) {
  const isJoin = e.type === 'join';
  const icon   = isJoin ? '🟢' : '🔴';
  const action = isJoin ? ` <strong>${esc(e.car||'?')}</strong>` : t('ev_left');
  const ts     = e.ts ? e.ts.slice(11, 19) || e.ts.slice(0,16) : '';
  const tsDate = e.ts ? e.ts.slice(0, 10) : '';
  if (compact) {
    return `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px">
      <span>${icon}</span>
      <span style="flex:1"><strong>${esc(e.driver)}</strong> ${action}</span>
      <span style="color:var(--muted);font-size:11px;flex-shrink:0">${ts}</span>
    </div>`;
  }
  return `<div style="display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:7px;background:var(--bg3);border-left:3px solid ${isJoin?'#27ae60':'var(--red)'}">
    <span style="font-size:18px">${icon}</span>
    <div style="flex:1;min-width:0">
      <div style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(e.driver)}</div>
      <div style="font-size:11px;color:var(--muted)">${action}</div>
    </div>
    <div style="text-align:right;flex-shrink:0">
      <div style="font-size:12px;font-weight:600">${ts}</div>
      <div style="font-size:10px;color:var(--muted)">${tsDate}</div>
    </div>
  </div>`;
}

function renderEvents() {
  const filter = (document.getElementById('ev-filter')||{}).value || '';
  const search = ((document.getElementById('ev-search')||{}).value || '').toLowerCase();
  let evs = _allEvents;
  if (filter) evs = evs.filter(e => e.type === filter);
  if (search) evs = evs.filter(e => (e.driver||'').toLowerCase().includes(search) || (e.car||'').toLowerCase().includes(search));
  const log = document.getElementById('event-log');
  if (!log) return;
  const cnt = document.getElementById('ev-count');
  if (cnt) cnt.textContent = evs.length ? `(${evs.length})` : '';
  if (!evs.length) {
    const msg = _allEvents.length === 0
      ? 'Keine Join/Leave-Events im Journal gefunden.<br><span style="font-size:11px">Tipp: Server muss laufen und Spieler müssen sich verbunden haben.</span>'
      : 'Keine Events für diesen Filter gefunden.';
    log.innerHTML = `<div style="color:var(--muted);font-size:13px;text-align:center;padding:48px">${msg}</div>`;
    return;
  }
  log.innerHTML = evs.map(e => _evRow(e, false)).join('');
}

function _renderDashEvents(evs) {
  const box = document.getElementById('dash-event-log');
  if (!box) return;
  if (!evs.length) {
    box.innerHTML = `<div style="color:var(--muted);font-size:12px;padding:8px 0">—</div>`;
    return;
  }
  box.innerHTML = evs.slice(0, 10).map(e => _evRow(e, true)).join('');
}

function loadEvents() {
  apiFetch('/api/events?limit=500')
    .then(r => r.json())
    .then(d => {
      const cnt = document.getElementById('ev-count');
      if (!d.ok) {
        if (cnt) cnt.textContent = '(Fehler)';
        const log = document.getElementById('event-log');
        if (log) log.innerHTML = `<div style="color:var(--red);font-size:13px;text-align:center;padding:48px">Fehler beim Laden: ${esc(d.msg||'unbekannt')}</div>`;
        return;
      }
      _allEvents = d.events || [];
      const st = document.getElementById('ev-status');
      if (st && d.lines_read != null) st.textContent = t('ev_status_text', d.lines_read, d.total) + ' ' + new Date().toLocaleTimeString();
      renderEvents();
      _renderDashEvents(_allEvents.slice(0, 10));
    }).catch(e => {
      const log = document.getElementById('event-log');
      if (log) log.innerHTML = `<div style="color:var(--red);font-size:13px;text-align:center;padding:48px">Netzwerkfehler beim Laden der Events</div>`;
    });
}

// Real-time detection from live poll diffs
function _detectLiveEvents(drivers) {
  if (_prevSnapshot === null) { // first poll — just record, don't fire events
    _prevSnapshot = {};
    (drivers||[]).forEach(drv => { _prevSnapshot[drv.id] = {name: drv.name, model: drv.model}; });
    return;
  }
  const now = new Date();
  const ts = `${now.toISOString().slice(0,10)} ${now.toTimeString().slice(0,8)}`;
  const newIds = new Set((drivers||[]).map(d => d.id));
  const prevIds = new Set(Object.keys(_prevSnapshot).map(Number));

  for (const drv of (drivers||[])) {
    if (!prevIds.has(drv.id)) {
      const ev = {type:'join', ts, driver: drv.name, guid: drv.guid||'', car: drv.model||''};
      _allEvents.unshift(ev);
    }
  }
  for (const [id, info] of Object.entries(_prevSnapshot)) {
    if (!newIds.has(Number(id))) {
      const ev = {type:'leave', ts, driver: info.name};
      _allEvents.unshift(ev);
    }
  }
  _prevSnapshot = {};
  (drivers||[]).forEach(drv => { _prevSnapshot[drv.id] = {name: drv.name, model: drv.model}; });

  // Refresh displays if something changed
  const evPanel = document.getElementById('p-events');
  if (evPanel && evPanel.classList.contains('active')) renderEvents();
  _renderDashEvents(_allEvents.slice(0, 10));
}

// ═══ UPTIME ═══════════════════════════════════════════════════════════════
function refreshUptime() {
  apiFetch("/api/uptime").then(r=>r.json()).then(d => {
    const s = d.uptime || "—";
    document.getElementById("h-uptime").textContent = s !== "unknown" ? "⏱ "+s : "";
    document.getElementById("i-uptime").textContent = s;
  }).catch(()=>{});
}

// ═══ CHAT ═════════════════════════════════════════════════════════════════
function _renderChat(msgs, boxId) {
  const box = document.getElementById(boxId);
  if (!box || !msgs.length) return;
  const atBottom = box.scrollHeight - box.scrollTop <= box.clientHeight + 30;
  box.innerHTML = msgs.map(m => {
    const text = esc(m.text||""), ts = esc(m.time||"");
    const match = text.match(/^(.+?)\s*\((\d+)\):\s*(.*)$/);
    if (match) return `<div style="display:flex;gap:6px;align-items:baseline"><span style="color:var(--muted);font-size:10px;flex-shrink:0">${ts}</span><span style="color:var(--red);font-weight:700;flex-shrink:0">${esc(match[1])}</span><span>${esc(match[3])}</span></div>`;
    return `<div style="color:var(--muted);font-size:11px">${ts} ${text}</div>`;
  }).join("");
  if (atBottom) box.scrollTop = box.scrollHeight;
}

// ═══ MAP ══════════════════════════════════════════════════════════════════
function loadMapImage() {
  if (mapImg || _mapLoading) return;
  _mapLoading = true;
  const img = new Image();
  img.onload = () => { mapImg = img; _mapLoading = false; drawMap(live); };
  img.onerror = () => { _mapLoading = false; };
  img.src = "/map";
}
function drawMap(data) {
  const c = document.getElementById("map-canvas");
  if (!c) return;
  const ctx = c.getContext("2d"), W = c.width, H = c.height;
  ctx.clearRect(0,0,W,H); ctx.fillStyle = "#0d0d0d"; ctx.fillRect(0,0,W,H);
  if (!mapImg) { ctx.fillStyle="#444";ctx.font="13px system-ui";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText("Lade Streckenübersicht...",W/2,H/2); loadMapImage(); return; }
  const pad=20, scale=Math.min((W-pad*2)/mapImg.width,(H-pad*2)/mapImg.height);
  const iw=mapImg.width*scale, ih=mapImg.height*scale, ix=(W-iw)/2, iy=(H-ih)/2;
  ctx.fillStyle="#111"; ctx.fillRect(ix-4,iy-4,iw+8,ih+8);
  ctx.globalAlpha=0.6; ctx.drawImage(mapImg,ix,iy,iw,ih); ctx.globalAlpha=1;
  const drivers = data?.drivers||[];
  if (!drivers.length) { document.getElementById("map-hint").textContent="Karte geladen · Keine Fahrer"; return; }
  const perim=2*(iw+ih);
  drivers.forEach((d,i) => {
    const sp=d.spLine||0, col=COLORS[i%COLORS.length], dist=sp*perim;
    let x,y;
    if(dist<=iw){x=ix+dist;y=iy;}else if(dist<=iw+ih){x=ix+iw;y=iy+(dist-iw);}else if(dist<=2*iw+ih){x=ix+iw-(dist-iw-ih);y=iy+ih;}else{x=ix;y=iy+ih-(dist-2*iw-ih);}
    const g=ctx.createRadialGradient(x,y,0,x,y,15);g.addColorStop(0,col+"90");g.addColorStop(1,"transparent");
    ctx.fillStyle=g;ctx.beginPath();ctx.arc(x,y,15,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.fillStyle=col;ctx.fill();ctx.strokeStyle="#fff";ctx.lineWidth=2;ctx.stroke();
    ctx.font="bold 10px system-ui";ctx.textAlign="center";
    ctx.fillStyle="rgba(0,0,0,.7)";ctx.fillText(d.name.slice(0,12),x+1,y-12);
    ctx.fillStyle="#fff";ctx.fillText(d.name.slice(0,12),x,y-13);
  });
  document.getElementById("map-hint").textContent=`${drivers.length} Fahrer`;
}



// ═══ SETTINGS OVERVIEW (extra_cfg) ════════════════════════════════════════
function loadOverviewExtraCfg() {
  const el  = document.getElementById('ov-extra-content');
  const st  = document.getElementById('ov-extra-status');
  if (!el) return;
  el.textContent = t('loading') || 'Lade...';
  apiFetch('/api/extra_cfg').then(r => r.json()).then(d => {
    if (!d.ok) { el.textContent = '✗ Fehler'; return; }
    const cfg = d.data || {};
    const bool = v => v === 'True' || v === true || v === '1'
      ? '<span style="color:var(--green)">✓</span>'
      : '<span style="color:var(--muted)">✗</span>';
    el.innerHTML = `<table class="itbl">
      <tr><td>Server Details</td><td>${bool(cfg.EnableServerDetails)}</td></tr>
      <tr><td>Anti-AFK</td><td>${bool(cfg.EnableAntiAfk)} ${cfg.MaxAfkTimeMinutes ? cfg.MaxAfkTimeMinutes+'min' : ''}</td></tr>
      <tr><td>Max Ping</td><td>${cfg.MaxPing || '—'}ms</td></tr>
      <tr><td>WeatherFX</td><td>${bool(cfg.EnableWeatherFx)}</td></tr>
      <tr><td>Real Time</td><td>${bool(cfg.EnableRealTime)}</td></tr>
      <tr><td>Client Messages</td><td>${bool(cfg.EnableClientMessages)}</td></tr>
      <tr><td>Min CSP</td><td>${cfg.MinimumCSPVersion || '0 (kein Limit)'}</td></tr>
      <tr><td>RCON Port</td><td>${cfg.RconPort || '9700'}</td></tr>
      ${cfg.UDPPluginAddress ? `<tr><td>UDP Plugin</td><td>${cfg.UDPPluginAddress}</td></tr>` : ''}
    </table>`;
    if (st) st.textContent = '✓ geladen';
  }).catch(() => { if (el) el.textContent = '✗ Ladefehler'; });
}
// ═══ SUN ANGLE PRESETS ═════════════════════════════════════════════════════
function setSunAngle(val) {
  const slider = document.getElementById('sv-sun');
  const label  = document.getElementById('sun-val');
  if (slider) slider.value = val;
  if (label)  label.textContent = val;
}
// ═══ TRACK PREVIEW ════════════════════════════════════════════════════════
function loadTrackPreview() {
  const track=document.getElementById("s-track").value, layout=document.getElementById("s-layout").value;
  const img=document.getElementById("track-preview");
  img.src=layout?`/track_img/${track}/${layout}`:`/track_img/${track}`;
  img.style.display="block"; img.onerror=()=>img.style.display="none";
  const infoUrl=layout?`/api/track_info/${track}/${layout}`:`/api/track_info/${track}`;
  apiFetch(infoUrl).then(r=>r.json()).then(d=>{
    document.getElementById("ti-length").textContent=d.length?d.length+"m":"";
    document.getElementById("ti-pits").textContent=d.pitboxes?d.pitboxes+" pits":"";
    document.getElementById("track-info-bar").style.display=(d.length||d.pitboxes)?"flex":"none";
  }).catch(()=>{});
}

function updateLayouts() {
  const sel=document.getElementById("s-track"), opt=sel.options[sel.selectedIndex];
  const layouts=JSON.parse(opt.dataset.layouts||"[]");
  const lSel=document.getElementById("s-layout");
  lSel.innerHTML='<option value="">(kein)</option>';
  layouts.forEach(l=>{const o=new Option(l,l);lSel.appendChild(o);});
  const cur=window.AC.trackLayout;
  if(cur&&[...lSel.options].some(o=>o.value===cur)) lSel.value=cur;
}

// ═══ CAR CONFIG (skin/ballast/restrictor) ════════════════════════════════
function toggleCar(el, event) {
  el.querySelector('input[type=checkbox]').click();
}

function toggleCarCfg(btn, carId) {
  const cfg = document.getElementById('cfg-'+carId);
  if (!cfg) return;
  const isOpen = cfg.classList.contains('open');
  cfg.classList.toggle('open', !isOpen);
  if (!isOpen) loadCarSkins(carId);
}

function loadCarSkins(carId) {
  const sel = document.getElementById('skin-'+carId);
  if (!sel || sel.dataset.loaded) return;
  apiFetch('/api/car_skins/'+carId).then(r=>r.json()).then(d=>{
    sel.innerHTML = d.skins.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('');
    sel.dataset.loaded = '1';
  }).catch(()=>{});
}

function getCarConfig() {
  const result = {};
  document.querySelectorAll('#car-list input[type=checkbox]:checked').forEach(cb => {
    const carId = cb.value;
    const skinEl = document.getElementById('skin-'+carId);
    const ballastEl = document.getElementById('ballast-'+carId);
    const restrictorEl = document.getElementById('restrictor-'+carId);
    result[carId] = {
      skin: skinEl?.value || '',
      ballast: parseInt(ballastEl?.value||'0'),
      restrictor: parseInt(restrictorEl?.value||'0'),
    };
  });
  return result;
}

// ═══ KICK / BAN ═══════════════════════════════════════════════════════════
function kick(id, name) {
  if (!confirm(`Kick ${name}?`)) return;
  apiFetch("/api/kick",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify({car_id:id})})
    .then(r=>r.json()).then(d=>toast(d.ok?"✓ "+name+" kicked":"✗ "+d.msg,d.ok?"ok":"err"));
}
function ban(id, guid, name) {
  if (!confirm(`Ban ${name}? GUID: ${guid}`)) return;
  apiFetch("/api/ban",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify({car_id:id,guid,name})})
    .then(r=>r.json()).then(d=>toast(d.ok?"✓ "+name+" banned":"✗ "+d.msg,d.ok?"ok":"err"));
}

// ═══ SETTINGS SAVES ═══════════════════════════════════════════════════════
function saveServerSettings() {
  const data = {NAME:document.getElementById("sv-name").value, REGISTER_TO_LOBBY:document.getElementById("sv-lobby").checked?"1":"0", SUN_ANGLE:document.getElementById("sv-sun").value, restart:autoRestart()};
  const pass=document.getElementById("sv-pass").value; if(pass) data.PASSWORD=pass;
  const ap=document.getElementById("sv-adminpass").value; if(ap) data.ADMIN_PASSWORD=ap;
  apiFetch("/save_server_settings",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(r=>r.json()).then(d=>toast(d.ok?t('t_saved'):'✗ '+d.msg,d.ok?'ok':'err'));
}

function saveTrackCars() {
  const cars=[...document.querySelectorAll("#car-list input:checked")].map(i=>i.value);
  if(!cars.length){toast(t('t_select_car'),"err");return;}
  const spc=parseInt(document.getElementById("slots-per-car").value)||2;
  const car_config=getCarConfig();
  apiFetch("/save_config",{method:"POST",headers:{'Content-Type':'application/json'},
    body:JSON.stringify({track:document.getElementById("s-track").value, layout:document.getElementById("s-layout").value, cars, slots_per_car:spc, car_config, restart:autoRestart()})})
    .then(r=>r.json()).then(d=>toast(d.ok?t('t_saved'):'✗ '+d.msg,d.ok?'ok':'err'));
}

function saveAssists() {
  const data={
    ABS_ALLOWED:document.getElementById("a-abs").checked?"1":"0",
    TC_ALLOWED:document.getElementById("a-tc").checked?"1":"0",
    STABILITY_ALLOWED:document.getElementById("a-stab").checked?"1":"0",
    AUTOCLUTCH_ALLOWED:document.getElementById("a-clutch").checked?"1":"0",
    TYRE_BLANKETS_ALLOWED:document.getElementById("a-blanket").checked?"1":"0",
    FORCE_VIRTUAL_MIRROR:document.getElementById("a-mirror").checked?"1":"0",
    FUEL_RATE:document.getElementById("v-fuel").value,
    DAMAGE_MULTIPLIER:document.getElementById("v-dmg").value,
    TYRE_WEAR_RATE:document.getElementById("v-tyre").value,
    ALLOWED_TYRES_OUT:document.getElementById("v-tyreout").value,
    MAX_CLIENTS:document.getElementById("v-maxcl").value,
    restart:autoRestart()
  };
  apiFetch("/save_assists",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(r=>r.json()).then(d=>toast(d.ok?t('t_saved'):'✗ '+d.msg,d.ok?'ok':'err'));
}

function saveSessions() {
  const data={practice_time:document.getElementById("prc-time").value,practice_open:document.getElementById("prc-open").checked,qualify_time:document.getElementById("qlf-time").value,qualify_open:document.getElementById("qlf-open").checked,race_laps:document.getElementById("race-laps").value,race_wait:document.getElementById("race-wait").value,restart:autoRestart()};
  apiFetch("/save_session",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(r=>r.json()).then(d=>toast(d.ok?t('t_saved'):'✗ '+d.msg,d.ok?'ok':'err'));
}

function saveWeather() {
  const data={weather_0_graphics:document.getElementById("w0-graphics").value,weather_0_ambient:document.getElementById("w0-amb").value,weather_0_road:document.getElementById("w0-road").value,weather_1_graphics:document.getElementById("w1-graphics").value,weather_1_ambient:document.getElementById("w1-amb").value,weather_1_road:document.getElementById("w1-road").value,restart:autoRestart()};
  apiFetch("/save_weather",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(r=>r.json()).then(d=>toast(d.ok?t('t_saved'):'✗ '+d.msg,d.ok?'ok':'err'));
}

function saveDynamicTrack() {
  const data={SESSION_START:document.getElementById("dt-start").value,RANDOMNESS:document.getElementById("dt-rand").value,SESSION_TRANSFER:document.getElementById("dt-transfer").value,LAP_GAIN:document.getElementById("dt-lap").value,restart:autoRestart()};
  apiFetch("/save_dynamic_track",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(r=>r.json()).then(d=>toast(d.ok?t('t_saved'):'✗ '+d.msg,d.ok?'ok':'err'));
}

// ═══ SERVER PROFILE ═══════════════════════════════════════════════════════
function updateWelcomePreview() {
  const txt = document.getElementById('welcome-msg').value;
  document.getElementById('welcome-preview').textContent = txt;
  document.getElementById('welcome-chars').textContent = txt.length + ' Zeichen';
}

function insertWelcomeText(text) {
  const ta = document.getElementById('welcome-msg');
  const start = ta.selectionStart, end = ta.selectionEnd;
  ta.value = ta.value.slice(0, start) + text + ta.value.slice(end);
  ta.selectionStart = ta.selectionEnd = start + text.length;
  ta.focus();
  updateWelcomePreview();
}

function insertWelcomeVar(key) {
  const vals = { NAME: document.getElementById('sv-name')?.value || '[Servername]', TRACK: document.getElementById('s-track')?.options[document.getElementById('s-track')?.selectedIndex]?.text || '[Strecke]' };
  insertWelcomeText(vals[key] || key);
}

async function loadServerProfile() {
  try {
    const d=await apiFetch("/api/server_profile").then(r=>r.json());
    document.getElementById("welcome-msg").value=d.welcome||"";
    updateWelcomePreview();
    const img=document.getElementById("logo-preview"), ph=document.getElementById("logo-placeholder");
    if(d.has_logo){img.src="/api/server_logo?"+Date.now();img.style.display="";ph.style.display="none";}
    else{img.style.display="none";ph.style.display="flex";}
  }catch(e){}
}
function previewLogo(inp){const f=inp.files[0];if(!f)return;const img=document.getElementById("logo-preview"),ph=document.getElementById("logo-placeholder");img.src=URL.createObjectURL(f);img.style.display="";ph.style.display="none";}
async function uploadLogo(){const inp=document.getElementById("logo-inp");if(!inp.files[0]){toast(t('t_no_image'),"err");return;}const fd=new FormData();fd.append("logo",inp.files[0]);const r=await fetch("/api/server_logo",{method:"POST",credentials:'same-origin',body:fd});const d=await r.json();toast(d.ok?t('t_logo_saved'):d.msg,d.ok?'ok':'err');}
async function saveServerProfile(){
  const welcome=document.getElementById("welcome-msg").value;
  const r=await apiFetch("/api/server_profile",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify({welcome})});
  const d=await r.json();
  toast(d.ok?t('t_profile_saved'):'✗ '+d.msg,d.ok?'ok':'err');
}

function saveExtendedSettings() {
  const bool = id => document.getElementById(id).checked ? '1' : '0';
  const val  = id => document.getElementById(id).value;
  const data = {
    UDP_PORT: val('sv-udp'), TCP_PORT: val('sv-udp'), HTTP_PORT: val('sv-http'),
    PICKUP_MODE_ENABLED: bool('sv-pickup'), LOOP_MODE: bool('sv-loop'),
    BLACKLIST_MODE: bool('sv-blacklist'),
    KICK_QUORUM: val('sv-kickq'), VOTING_QUORUM: val('sv-voteq'),
    VOTE_DURATION: val('sv-vdur'), RACE_OVER_TIME: val('sv-raceover'),
    CLIENT_SEND_INTERVAL_HZ: val('sv-hz'),
    LEGAL_TYRES: val('sv-tyres'),
    ALLOWED_TYRES_OUT: val('v-tyreout'),
    restart: autoRestart()
  };
  apiFetch('/save_server_settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(r=>r.json()).then(d=>toast(d.ok?t('t_saved'):'✗ '+d.msg,d.ok?'ok':'err'));
}

// ═══ PRESETS ══════════════════════════════════════════════════════════════
function loadPresetList(){
  apiFetch("/api/presets").then(r=>r.json()).then(presets=>{
    const el=document.getElementById("preset-list"),keys=Object.keys(presets);
    if(!keys.length){el.innerHTML=`<div style="color:var(--muted);font-size:13px">${t('t_no_presets')}</div>`;return;}
    el.innerHTML=keys.map(name=>{const p=presets[name];return `<div class="preset-item"><div><div class="preset-name">${esc(name)}</div><div class="preset-info">${esc(p.track||"")}${p.layout?" / "+esc(p.layout):""} · ${esc(p.saved||"")}</div></div><div class="preset-acts"><button class="btn btn-green btn-sm" onclick="applyPreset('${esc(name)}')">▶ Load</button><button class="btn btn-danger btn-sm" onclick="removePreset('${esc(name)}')">✕</button></div></div>`;}).join("");
  });
}
function savePreset(){const name=document.getElementById("preset-name").value.trim();if(!name){toast("Name eingeben","err");return;}apiFetch("/api/presets",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}).then(r=>r.json()).then(d=>{toast(d.ok?"✓ "+d.msg:"✗ "+d.msg,d.ok?"ok":"err");if(d.ok){document.getElementById("preset-name").value="";loadPresetList();}});}
function applyPreset(name){if(!confirm(`Preset "${name}" laden und Server neu starten?`))return;toast("Lade Preset...","info");apiFetch(`/api/presets/${encodeURIComponent(name)}/load`,{method:"POST"}).then(r=>r.json()).then(d=>{toast(d.ok?"✓ "+d.msg:"✗ "+d.msg,d.ok?"ok":"err");if(d.ok)setTimeout(refreshLive,3000);});}
function removePreset(name){if(!confirm(`Preset "${name}" löschen?`))return;apiFetch(`/api/presets/${encodeURIComponent(name)}`,{method:"DELETE"}).then(r=>r.json()).then(d=>{toast(d.ok?t('t_deleted'):"✗ "+d.msg,d.ok?"ok":"err");loadPresetList();});}

// ═══ CHAT SEND ════════════════════════════════════════════════════════════
function sendChat() {
  const inp = document.getElementById('chat-send-inp');
  const msg = inp.value.trim();
  if (!msg) return;
  apiFetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:msg})})
    .then(r=>r.json()).then(d=>{
      toast(d.ok?'✓ Gesendet':'✗ '+d.msg, d.ok?'ok':'err');
      if (d.ok) inp.value='';
    });
}

// ═══ LOGS + RCON ══════════════════════════════════════════════════════════
function loadLogs(){apiFetch("/logs").then(r=>r.json()).then(d=>{const box=document.getElementById("logbox");box.innerHTML=d.logs.split("\n").map(l=>{const s=esc(l);if(/ERR|FAIL|error/i.test(l))return`<span style="color:#ff6b6b">${s}</span>`;if(/WRN|WARN/i.test(l))return`<span style="color:var(--yellow)">${s}</span>`;if(/INF\b|INFO/i.test(l))return`<span style="color:#74b9ff">${s}</span>`;return s;}).join("\n");box.scrollTop=box.scrollHeight;});}

const _rconHistory = [];
function sendRcon() {
  const inp = document.getElementById('rcon-inp');
  const cmd = inp.value.trim();
  if (!cmd) return;
  const hist = document.getElementById('rcon-history');
  apiFetch('/api/rcon_console', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({cmd})})
    .then(r=>r.json()).then(d => {
      _rconHistory.push({cmd, ok:d.ok, resp:d.response});
      if (_rconHistory.length > 50) _rconHistory.shift();
      hist.innerHTML = _rconHistory.map(h =>
        `<div><span style="color:var(--muted)">›</span> <span style="color:var(--text)">${esc(h.cmd)}</span>`+
        (h.resp?` <span style="color:${h.ok?'var(--green)':'var(--red)'}">${esc(h.resp)}</span>`:'')+'</div>'
      ).join('');
      hist.scrollTop = hist.scrollHeight;
      inp.value = '';
    }).catch(()=>{});
}

// ═══ CONTENT LIBRARY ══════════════════════════════════════════════════════
let _clData = {cars:[], tracks:[]};
let _clTab   = 'all';
let _clSel   = new Set(); // "car:name" or "track:name"

function loadInstalledContent(){
  const cGrid = document.getElementById('cl-cars-grid');
  const tGrid = document.getElementById('cl-tracks-grid');
  if(cGrid) cGrid.innerHTML = `<div class="cl-empty">${t('loading')}</div>`;
  if(tGrid) tGrid.innerHTML = `<div class="cl-empty">${t('loading')}</div>`;
  _clSel.clear(); updateBatchBar();
  Promise.all([
    apiFetch('/api/installed_content').then(r=>r.json()),
    apiFetch('/api/disk_usage').then(r=>r.json()),
  ]).then(([d, du]) => {
    _clData = d;
    renderDiskUsage(du);
    renderContentLibrary();
  }).catch(()=>{
    if(cGrid) cGrid.innerHTML = `<div class="cl-empty">Fehler beim Laden</div>`;
  });
}

function renderDiskUsage(du){
  const bar = document.getElementById('du-bar');
  if(!bar) return;
  bar.style.display = 'flex';
  const fmt = mb => mb >= 1024 ? (mb/1024).toFixed(1)+' GB' : mb+' MB';
  document.getElementById('du-cars').textContent   = fmt(du.cars_mb||0);
  document.getElementById('du-tracks').textContent = fmt(du.tracks_mb||0);
  const freeLabel = document.getElementById('du-free-lbl');
  const freeBar   = document.getElementById('du-free-bar');
  if(freeLabel) freeLabel.textContent = du.free_gb + ' GB frei von ' + du.total_gb + ' GB';
  if(freeBar && du.total_gb > 0){
    const usedPct = Math.round((1 - du.free_gb/du.total_gb)*100);
    freeBar.style.width = Math.min(usedPct,100)+'%';
    freeBar.className = 'bar-fill ' + (usedPct>85 ? 'bar-hot' : usedPct>60 ? 'bar-ram' : 'bar-green');
  }
}

function setContentTab(el, tab){
  _clTab = tab;
  document.querySelectorAll('.cl-tab').forEach(b=>b.classList.remove('active'));
  el.classList.add('active');
  const carsSec   = document.getElementById('cl-cars-section');
  const tracksSec = document.getElementById('cl-tracks-section');
  if(carsSec)   carsSec.style.display   = (tab==='tracks') ? 'none' : '';
  if(tracksSec) tracksSec.style.display = (tab==='cars')   ? 'none' : '';
  filterContentLibrary();
}

function filterContentLibrary(){
  const q = (document.getElementById('cl-search')?.value || '').toLowerCase();
  renderContentLibrary(q);
}

function renderContentLibrary(q=''){
  const cars   = _clData.cars   || [];
  const tracks = _clData.tracks || [];
  const fcars   = q ? cars.filter(c   => (c.name+c.brand+c.id).toLowerCase().includes(q)) : cars;
  const ftracks = q ? tracks.filter(tr => (tr.name+tr.id).toLowerCase().includes(q))       : tracks;

  const cGrid = document.getElementById('cl-cars-grid');
  const tGrid = document.getElementById('cl-tracks-grid');
  const cCount = document.getElementById('cl-cars-count');
  const tCount = document.getElementById('cl-tracks-count');

  if(cCount) cCount.textContent = fcars.length ? `(${fcars.length})` : '';
  if(tCount) tCount.textContent = ftracks.length ? `(${ftracks.length})` : '';

  if(cGrid) cGrid.innerHTML = fcars.length
    ? fcars.map(c => carCard(c)).join('')
    : `<div class="cl-empty">Keine Autos installiert</div>`;

  if(tGrid) tGrid.innerHTML = ftracks.length
    ? ftracks.map(tr => trackCard(tr)).join('')
    : `<div class="cl-empty">Keine Strecken installiert</div>`;
}

function carCard(c){
  const selKey = `car:${c.id}`;
  const checked = _clSel.has(selKey) ? 'checked' : '';
  const activeCls = c.active ? ' active-content' : '';
  const selCls = _clSel.has(selKey) ? ' selected' : '';
  const validBadge = c.valid
    ? `<span class="cl-badge cl-badge-ok">✓ OK</span>`
    : `<span class="cl-badge cl-badge-warn">⚠ ${c.issues.length}</span>`;
  const activeBadge = c.active ? `<span class="cl-badge cl-badge-active">● Aktiv</span>` : '';
  const skinsBadge = `<span class="cl-badge cl-badge-info">🎨 ${c.skin_count}</span>`;
  return `<div class="cl-card${activeCls}${selCls}" onclick="openCarDetail('${esc(c.id)}')" id="clcard-car-${esc(c.id)}">
    <input type="checkbox" class="cl-cb" ${checked} onclick="event.stopPropagation();toggleClSelect('${esc(selKey)}',this)" title="Auswählen">
    <div class="cl-card-head">
      <img class="cl-thumb" src="/car_img/${esc(c.id)}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
      <div class="cl-thumb-placeholder" style="display:none">🚗</div>
      <div class="cl-card-info">
        <div class="cl-card-name">${esc(c.name)}</div>
        <div class="cl-card-sub">${esc(c.brand||c.id)}</div>
      </div>
    </div>
    <div class="cl-card-badges">${validBadge}${activeBadge}${skinsBadge}</div>
    <div class="cl-card-actions">
      <button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deleteContent('car','${esc(c.id)}')" title="Löschen">🗑</button>
    </div>
  </div>`;
}

function trackCard(tr){
  const selKey = `track:${tr.id}`;
  const checked = _clSel.has(selKey) ? 'checked' : '';
  const activeCls = tr.active ? ' active-content' : '';
  const selCls = _clSel.has(selKey) ? ' selected' : '';
  const validBadge = tr.valid
    ? `<span class="cl-badge cl-badge-ok">✓ OK</span>`
    : `<span class="cl-badge cl-badge-warn">⚠ ${tr.issues.length}</span>`;
  const activeBadge = tr.active ? `<span class="cl-badge cl-badge-active">● Aktiv</span>` : '';
  const layoutBadge = tr.layout_count > 1
    ? `<span class="cl-badge cl-badge-info">⊞ ${tr.layout_count}</span>`
    : '';
  const meta = [tr.length, tr.pitboxes ? tr.pitboxes+' Boxen' : ''].filter(Boolean).join(' · ');
  return `<div class="cl-card${activeCls}${selCls}" onclick="openTrackDetail('${esc(tr.id)}')" id="clcard-track-${esc(tr.id)}">
    <input type="checkbox" class="cl-cb" ${checked} onclick="event.stopPropagation();toggleClSelect('${esc(selKey)}',this)" title="Auswählen">
    <div class="cl-card-head">
      <img class="cl-thumb" src="/track_img/${esc(tr.id)}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
      <div class="cl-thumb-placeholder" style="display:none">🏁</div>
      <div class="cl-card-info">
        <div class="cl-card-name">${esc(tr.name)}</div>
        <div class="cl-card-sub">${meta || esc(tr.id)}</div>
      </div>
    </div>
    <div class="cl-card-badges">${validBadge}${activeBadge}${layoutBadge}</div>
    <div class="cl-card-actions">
      <button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deleteContent('track','${esc(tr.id)}')" title="Löschen">🗑</button>
    </div>
  </div>`;
}

// ── Batch selection ────────────────────────────────────────────────────────
function toggleClSelect(key, cb){
  if(cb.checked) _clSel.add(key); else _clSel.delete(key);
  const id = key.startsWith('car:') ? 'clcard-car-'+key.slice(4) : 'clcard-track-'+key.slice(6);
  document.getElementById(id)?.classList.toggle('selected', cb.checked);
  updateBatchBar();
}

function updateBatchBar(){
  const bar = document.getElementById('cl-batch');
  const cnt = document.getElementById('cl-batch-count');
  if(!bar) return;
  if(_clSel.size > 0){
    bar.classList.add('show');
    if(cnt) cnt.textContent = `${_clSel.size} ausgewählt`;
  } else {
    bar.classList.remove('show');
  }
}

function clearBatchSelection(){
  _clSel.clear();
  document.querySelectorAll('.cl-cb').forEach(cb => cb.checked=false);
  document.querySelectorAll('.cl-card.selected').forEach(el => el.classList.remove('selected'));
  updateBatchBar();
}

function batchDelete(){
  if(!_clSel.size) return;
  const names = [..._clSel].map(k => k.split(':')[1]).join(', ');
  if(!confirm(`${_clSel.size} Einträge löschen?\n${names}\n\nDies kann nicht rückgängig gemacht werden!`)) return;
  const tasks = [..._clSel].map(key => {
    const [type, name] = key.split(':');
    return apiFetch(`/api/delete_content/${type}/${encodeURIComponent(name)}`,{method:'DELETE'}).then(r=>r.json());
  });
  Promise.all(tasks).then(results => {
    const ok = results.filter(r=>r.ok).length;
    toast(`✓ ${ok} von ${results.length} gelöscht`, ok===results.length ? 'ok' : 'err');
    loadInstalledContent();
  }).catch(()=>toast('✗ Fehler','err'));
}

function deleteContent(type,name){
  if(!confirm(`"${name}" wirklich löschen?\n\nDies kann nicht rückgängig gemacht werden!`)) return;
  apiFetch(`/api/delete_content/${type}/${encodeURIComponent(name)}`,{method:'DELETE'})
    .then(r=>r.json()).then(d=>{
      toast(d.ok?`✓ ${d.msg}`:`✗ ${d.msg}`, d.ok?'ok':'err');
      if(d.ok) loadInstalledContent();
    }).catch(()=>toast('✗ Fehler','err'));
}

// ── Detail Modal ───────────────────────────────────────────────────────────
let _dmActiveTab = 0;

function openCarDetail(id){
  openDetailModal();
  apiFetch(`/api/content_detail/car/${encodeURIComponent(id)}`).then(r=>r.json()).then(d=>{
    renderCarDetail(d);
  });
}

function openTrackDetail(id){
  openDetailModal();
  apiFetch(`/api/content_detail/track/${encodeURIComponent(id)}`).then(r=>r.json()).then(d=>{
    renderTrackDetail(d);
  });
}

function openDetailModal(){
  _dmActiveTab = 0;
  document.getElementById('dm-title').textContent  = 'Lade...';
  document.getElementById('dm-brand').textContent  = '';
  document.getElementById('dm-badges').innerHTML   = '';
  document.getElementById('dm-tabs').innerHTML     = '';
  document.getElementById('dm-panes').innerHTML    = '';
  document.getElementById('dm-actions').innerHTML  = '';
  document.getElementById('dm-img-wrap').innerHTML = '<div class="dm-hero-placeholder">⏳</div>';
  document.getElementById('detail-modal').classList.add('show');
}

function closeDetailModal(){
  document.getElementById('detail-modal').classList.remove('show');
}

function _dmTab(tabs, panes, idx){
  tabs.forEach((b,i)=>b.classList.toggle('active',i===idx));
  panes.forEach((p,i)=>p.classList.toggle('active',i===idx));
}

function renderCarDetail(d){
  document.getElementById('dm-title').textContent = d.name || d.id;
  document.getElementById('dm-brand').textContent = d.brand || '';
  document.getElementById('dm-img-wrap').innerHTML =
    `<img class="dm-hero-img" src="/car_img/${esc(d.id)}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt=""><div class="dm-hero-placeholder" style="display:none">🚗</div>`;

  const badges = [];
  if(d.active)  badges.push(`<span class="cl-badge cl-badge-active">● Im Server aktiv</span>`);
  if(d.valid)   badges.push(`<span class="cl-badge cl-badge-ok">✓ Valide</span>`);
  else          badges.push(`<span class="cl-badge cl-badge-warn">⚠ ${d.issues.length} Problem(e)</span>`);
  if(d.size_mb) badges.push(`<span class="cl-badge cl-badge-info">${d.size_mb} MB</span>`);
  document.getElementById('dm-badges').innerHTML = badges.join('');

  // Tabs
  const tabLabels = ['Übersicht','Skins ('+d.skins.length+')','Validierung'];
  const tabsEl = document.getElementById('dm-tabs');
  tabsEl.innerHTML = tabLabels.map((lbl,i)=>`<button class="dm-tab${i===0?' active':''}" onclick="_dmTab(Array.from(this.parentElement.querySelectorAll('.dm-tab')),Array.from(document.getElementById('dm-panes').querySelectorAll('.dm-pane')),${i})">${lbl}</button>`).join('');

  // Overview pane
  const specItems = [
    d.class     ? {v:d.class,     l:'Klasse'}    : null,
    d.power     ? {v:d.power,     l:'Leistung'}  : null,
    d.torque    ? {v:d.torque,    l:'Drehmoment'}: null,
    d.weight    ? {v:d.weight,    l:'Gewicht'}   : null,
    d.topspeed  ? {v:d.topspeed,  l:'Top Speed'} : null,
    d.skins.length ? {v:d.skins.length, l:'Skins'} : null,
  ].filter(Boolean);
  const specsHtml = specItems.length
    ? `<div class="dm-spec-grid">${specItems.map(s=>`<div class="dm-spec"><div class="dm-spec-val">${esc(String(s.v))}</div><div class="dm-spec-lbl">${s.l}</div></div>`).join('')}</div>`
    : '';
  const descHtml = d.description
    ? `<div style="font-size:12px;color:var(--muted);line-height:1.7;margin-bottom:12px">${esc(d.description)}</div>`
    : '';
  const tagsHtml = d.tags?.length
    ? `<div style="display:flex;gap:5px;flex-wrap:wrap">${d.tags.map(tg=>`<span class="cl-badge cl-badge-info">${esc(tg)}</span>`).join('')}</div>`
    : '';

  // Skins pane
  const skinsHtml = d.skins.length
    ? `<div class="skin-grid">${d.skins.map(s=>{
        const img = s.has_livery ? `/skin_img/${esc(d.id)}/${esc(s.name)}` : '';
        return `<div class="skin-card">${img
          ? `<img class="skin-thumb" src="${img}" onerror="this.parentElement.style.display='none'" loading="lazy" alt="">`
          : `<div class="skin-thumb" style="display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:10px">No preview</div>`
        }<div class="skin-name" title="${esc(s.name)}">${esc(s.name)}</div></div>`;
      }).join('')}</div>`
    : `<div style="color:var(--muted);font-size:13px">Keine Skins gefunden.</div>`;

  // Validation pane
  const valHtml = d.valid
    ? `<div class="issue-row issue-ok">✓ Alle Pflichtdateien vorhanden</div>`
    : d.issues.map(i=>`<div class="issue-row issue-err">✗ ${esc(i)}</div>`).join('');

  document.getElementById('dm-panes').innerHTML =
    `<div class="dm-pane active">${specsHtml}${descHtml}${tagsHtml}</div>
     <div class="dm-pane">${skinsHtml}</div>
     <div class="dm-pane"><div class="issue-list">${valHtml}</div></div>`;

  // Actions
  const inServer = d.active;
  document.getElementById('dm-actions').innerHTML =
    `<button class="btn ${inServer?'btn-gray':'btn-green'}" onclick="closeDetailModal()" style="display:none" id="dm-server-btn"></button>
     <button class="btn btn-danger" onclick="closeDetailModal();deleteContent('car','${esc(d.id)}')">🗑 Löschen</button>
     <button class="btn btn-gray" onclick="closeDetailModal()">Schließen</button>`;
}

function renderTrackDetail(d){
  document.getElementById('dm-title').textContent = d.name || d.id;
  document.getElementById('dm-brand').textContent = d.layouts.length > 1 ? `${d.layouts.length} Layouts` : '';
  document.getElementById('dm-img-wrap').innerHTML =
    `<img class="dm-hero-img" src="/track_img/${esc(d.id)}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="" style="object-fit:contain"><div class="dm-hero-placeholder" style="display:none">🏁</div>`;

  const badges = [];
  if(d.active)  badges.push(`<span class="cl-badge cl-badge-active">● Im Server aktiv</span>`);
  if(d.valid)   badges.push(`<span class="cl-badge cl-badge-ok">✓ Valide</span>`);
  else          badges.push(`<span class="cl-badge cl-badge-warn">⚠ ${d.issues.length} Problem(e)</span>`);
  if(d.size_mb) badges.push(`<span class="cl-badge cl-badge-info">${d.size_mb} MB</span>`);
  document.getElementById('dm-badges').innerHTML = badges.join('');

  const tabLabels = ['Layouts','Validierung'];
  const tabsEl = document.getElementById('dm-tabs');
  tabsEl.innerHTML = tabLabels.map((lbl,i)=>`<button class="dm-tab${i===0?' active':''}" onclick="_dmTab(Array.from(this.parentElement.querySelectorAll('.dm-tab')),Array.from(document.getElementById('dm-panes').querySelectorAll('.dm-pane')),${i})">${lbl}</button>`).join('');

  const layoutsHtml = `<div class="layout-list">${d.layouts.map(lyt=>{
    const mapUrl = lyt.id
      ? `/track_img/${esc(d.id)}/${esc(lyt.id)}`
      : `/track_img/${esc(d.id)}`;
    const meta = [lyt.length, lyt.pitboxes?lyt.pitboxes+' Boxen':''].filter(Boolean).join(' · ');
    return `<div class="layout-item">
      <img class="layout-map" src="${mapUrl}" onerror="this.style.display='none'" alt="">
      <div class="layout-info">
        <div class="layout-name">${esc(lyt.name||lyt.id||d.name)}</div>
        ${meta?`<div class="layout-meta">${meta}</div>`:''}
      </div>
    </div>`;
  }).join('')}</div>`;

  const valHtml = d.valid
    ? `<div class="issue-row issue-ok">✓ Alle Pflichtdateien vorhanden</div>`
    : d.issues.map(i=>`<div class="issue-row issue-err">✗ ${esc(i)}</div>`).join('');

  document.getElementById('dm-panes').innerHTML =
    `<div class="dm-pane active">${layoutsHtml}</div>
     <div class="dm-pane"><div class="issue-list">${valHtml}</div></div>`;

  document.getElementById('dm-actions').innerHTML =
    `<button class="btn btn-danger" onclick="closeDetailModal();deleteContent('track','${esc(d.id)}')">🗑 Löschen</button>
     <button class="btn btn-gray" onclick="closeDetailModal()">Schließen</button>`;
}

// ═══ PLAYERS TAB ══════════════════════════════════════════════════════════
const GUID_ELEMS = {whitelist:{list:'wl-list',input:'wl-input'},admins:{list:'adm-list',input:'adm-input'},blacklist:{list:'bl-list',input:null}};

function loadGuidList(type){
  const cfg=GUID_ELEMS[type];
  apiFetch(`/api/${type}`).then(r=>r.json()).then(d=>{
    const el=document.getElementById(cfg.list);
    const guids=d.guids||[];
    if(!guids.length){el.innerHTML=`<div style="color:var(--muted);font-size:12px;padding:8px">${t('t_no_entries')}</div>`;return;}
    el.innerHTML=guids.map(g=>`<div class="guid-row"><span title="${esc(g)}">${esc(g.length>30?g.slice(0,28)+'…':g)}</span><button class="btn btn-danger btn-sm" onclick="removeGuid('${type}','${esc(g)}')">✕</button></div>`).join("");
  }).catch(()=>{});
}

function addGuid(type){
  const cfg=GUID_ELEMS[type];
  const inp=document.getElementById(cfg.input);
  const guid=(inp?.value||"").trim();
  if(!guid){toast("GUID eingeben","err");return;}
  apiFetch(`/api/${type}`,{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify({guid})})
    .then(r=>r.json()).then(d=>{toast(d.ok?"✓ Hinzugefügt":"✗ "+d.msg,d.ok?"ok":"err");if(d.ok){inp.value="";loadGuidList(type);}});
}

function removeGuid(type,guid){
  apiFetch(`/api/${type}/${encodeURIComponent(guid)}`,{method:"DELETE"})
    .then(r=>r.json()).then(d=>{toast(d.ok?"✓ Entfernt":"✗ "+d.msg,d.ok?"ok":"err");if(d.ok)loadGuidList(type);});
}

// ═══ ADVANCED TAB ═════════════════════════════════════════════════════════
function loadExtraCfg(){
  apiFetch("/api/extra_cfg").then(r=>r.json()).then(d=>{
    const c=d.data||{};
    const setTog=(id,key,def)=>{const el=document.getElementById(id);if(el)el.checked=c[key]!==undefined?(c[key]==="true"||c[key]===true):def;};
    const setNum=(id,key,def)=>{const el=document.getElementById(id);if(el)el.value=c[key]!==undefined?c[key]:def;};
    const setStr=(id,key,def)=>{const el=document.getElementById(id);if(el)el.value=c[key]!==undefined?c[key]:def;};
    setTog("ec-details","EnableServerDetails",true);
    setTog("ec-afk","EnableAntiAfk",true);
    setNum("ec-afkmin","MaxAfkTimeMinutes",10);
    setNum("ec-ping","MaxPing",500);
    setTog("ec-lights","ForceLights",false);
    setTog("ec-wxfx","EnableWeatherFx",false);
    setTog("ec-clmsg","EnableClientMessages",true);
    setTog("ec-realtime","EnableRealTime",false);
    setNum("ec-csp","MinimumCSPVersion",0);
    setNum("ec-sec","MandatoryClientSecurityLevel",0);
    setNum("ec-rcon","RconPort",9700);
    setStr("ec-loadimg","LoadingImageUrl","");
  }).catch(()=>{});
}

function saveExtraCfg(){
  const boolStr=id=>document.getElementById(id).checked?"true":"false";
  const numVal=id=>document.getElementById(id).value;
  const strVal=id=>document.getElementById(id).value;
  const data={EnableServerDetails:boolStr("ec-details"),EnableAntiAfk:boolStr("ec-afk"),MaxAfkTimeMinutes:parseInt(numVal("ec-afkmin")),MaxPing:parseInt(numVal("ec-ping")),ForceLights:boolStr("ec-lights"),EnableWeatherFx:boolStr("ec-wxfx"),EnableClientMessages:boolStr("ec-clmsg"),EnableRealTime:boolStr("ec-realtime"),MinimumCSPVersion:parseInt(numVal("ec-csp")),MandatoryClientSecurityLevel:parseInt(numVal("ec-sec")),RconPort:parseInt(numVal("ec-rcon")),LoadingImageUrl:strVal("ec-loadimg")};
  apiFetch("/api/extra_cfg",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(r=>r.json()).then(d=>{
      toast(d.ok?t('t_extra_cfg_saved'):'✗ '+d.msg,d.ok?'ok':'err');
      if(d.ok)ctrl('restart');
    });
}

function loadDiscord(){apiFetch("/api/discord").then(r=>r.json()).then(d=>{document.getElementById("discord-url").value=d.url||"";}).catch(()=>{});}
function saveDiscord(){const url=document.getElementById("discord-url").value.trim();apiFetch("/api/discord",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify({url})}).then(r=>r.json()).then(d=>toast(d.ok?t('t_webhook_saved'):'✗ '+d.msg,d.ok?'ok':'err'));}
function testDiscord(){apiFetch("/api/discord/test",{method:"POST"}).then(r=>r.json()).then(d=>toast(d.ok?"✓ "+d.msg:"✗ "+d.msg,d.ok?"ok":"err"));}

async function restoreBackup(){
  const inp=document.getElementById("restore-inp");
  if(!inp.files[0]){toast("Keine Datei gewählt","err");return;}
  if(!confirm("Config wirklich wiederherstellen? Server wird neu gestartet!"))return;
  const fd=new FormData();fd.append("backup",inp.files[0]);
  toast("Restore läuft...","info");
  const r=await fetch("/api/restore",{method:"POST",credentials:'same-origin',body:fd});
  const d=await r.json();
  toast(d.ok?"✓ "+d.msg:"✗ "+d.msg,d.ok?"ok":"err");
}

// ═══ ZIP UPLOAD ═══════════════════════════════════════════════════════════
function handleDrop(event){const f=event.dataTransfer.files[0];if(!f)return;if(!f.name.toLowerCase().endsWith(".zip")){toast("✗ Nur ZIP Dateien","err");return;}uploadZip(f);}
function uploadZip(file){
  if(!file)file=document.getElementById("zip-inp").files[0];if(!file)return;
  const fd=new FormData();fd.append("file",file);
  const prog=document.getElementById("up-prog"),msg=document.getElementById("up-msg"),pct=document.getElementById("up-pct"),bar=document.getElementById("up-bar");
  prog.style.display="block";msg.textContent=file.name;bar.style.width="0%";pct.textContent="0%";
  const xhr=new XMLHttpRequest();xhr.open("POST","/upload");
  xhr.upload.onprogress=e=>{if(!e.lengthComputable)return;const p=Math.round(e.loaded/e.total*100);bar.style.width=p+"%";pct.textContent=p+"%";};
  xhr.onload=()=>{
    prog.style.display="none";let d;try{d=JSON.parse(xhr.responseText);}catch(e){toast("✗ Server Fehler","err");return;}
    if(!d.ok){toast("✗ "+d.msg,"err");return;}
    currentZip=d.filename;document.getElementById("zip-name").textContent=d.filename;
    document.getElementById("z-cars").innerHTML=d.cars.length?d.cars.map(c=>`<div class="ci"><input type="checkbox" value="${esc(c)}" checked><img class="ci-img" src="/car_img/${esc(c)}" onerror="this.style.display='none'" alt=""><div class="ci-info"><span class="ci-name">${esc(c)}</span></div></div>`).join(""):`<div style="padding:8px;color:var(--muted)">${t('t_no_cars')}</div>`;
    document.getElementById("z-tracks").innerHTML=d.tracks.length?d.tracks.map(t=>`<div class="ci"><input type="checkbox" value="${esc(t)}" checked><div class="ci-info"><span class="ci-name">${esc(t)}</span></div></div>`).join(""):`<div style="padding:8px;color:var(--muted)">${t('t_no_tracks')}</div>`;
    document.getElementById("up-modal").classList.add("show");document.getElementById("zip-inp").value="";
  };
  xhr.onerror=()=>{prog.style.display="none";toast("✗ Upload fehlgeschlagen","err");};
  xhr.send(fd);
}
function doImport(){
  const cars=[...document.querySelectorAll("#z-cars input:checked")].map(i=>i.value);
  const tracks=[...document.querySelectorAll("#z-tracks input:checked")].map(i=>i.value);
  if(!cars.length&&!tracks.length){toast(t('t_nothing_selected'),"err");return;}
  toast(t('t_importing'),"info");
  apiFetch("/import_zip",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:currentZip,cars,tracks})})
    .then(r=>r.json()).then(d=>{if(d.ok){toast("✓ Importiert: "+d.imported.join(", "));closeModal();setTimeout(()=>location.reload(),2500);}else toast("✗ "+d.msg,"err");});
}
function closeModal(){document.getElementById("up-modal").classList.remove("show");}

// Schließen beim Klick auf Modal-Hintergrund
document.addEventListener('click', e => {
  if(e.target.id === 'detail-modal') closeDetailModal();
  if(e.target.id === 'up-modal') closeModal();
  if(e.target.id === 'folder-modal') closeFolderModal();
});

// ═══ FOLDER UPLOAD ════════════════════════════════════════════════════════
let _folderType=null,_folderFiles=null;
function setFolderType(type){_folderType=type;document.getElementById("type-car-btn").className="btn "+(type==="car"?"btn-red":"btn-gray");document.getElementById("type-track-btn").className="btn "+(type==="track"?"btn-red":"btn-gray");const dz=document.getElementById("folder-dz");dz.style.opacity="1";dz.style.pointerEvents="auto";document.getElementById("folder-dz-hint").textContent=type==="car"?t('t_folder_car'):t('t_folder_track');}
function handleFolderDrop(event){if(!_folderType){toast(t('t_select_type'),"err");return;}const items=event.dataTransfer.items;if(!items)return;const allFiles=[];let pending=0;function traverse(entry,path){if(entry.isFile){pending++;entry.file(f=>{Object.defineProperty(f,"webkitRelativePath",{value:path+f.name});allFiles.push(f);if(--pending===0)showFolderModal(allFiles);});}else if(entry.isDirectory){const reader=entry.createReader();pending++;reader.readEntries(entries=>{pending--;entries.forEach(e=>traverse(e,path+entry.name+"/"));if(pending===0)showFolderModal(allFiles);});}}for(let i=0;i<items.length;i++){const entry=items[i].webkitGetAsEntry();if(entry)traverse(entry,"");}}
function analyzeFolder(files){if(!_folderType){toast(t('t_select_type'),"err");return;}showFolderModal(Array.from(files));}
function showFolderModal(files){if(!files.length)return;_folderFiles=files;const firstPath=files[0].webkitRelativePath||files[0].name;const rootName=firstPath.split("/")[0]||"content";document.getElementById("fm-type-label").textContent=_folderType==="car"?"Auto":"Strecke";document.getElementById("fm-root-name").value=rootName;updateFolderTarget();const dirs={};let rootFiles=0;files.forEach(f=>{const rel=(f.webkitRelativePath||f.name).split("/").slice(1).join("/");const parts=rel.split("/");if(parts.length===1){rootFiles++;return;}const dir=parts[0];dirs[dir]=(dirs[dir]||0)+1;});let html="";if(rootFiles>0)html+=`<div style="color:var(--muted)">📄 ${rootFiles} Datei(en) im Stammordner</div>`;Object.entries(dirs).sort().forEach(([d,n])=>{html+=`<div>📁 ${esc(d)}/ <span style="color:var(--muted)">(${n} Datei${n>1?"en":""})</span></div>`;});document.getElementById("fm-tree").innerHTML=html||"<div style='color:var(--muted)'>Keine Dateien</div>";document.getElementById("fm-upload-prog").style.display="none";document.getElementById("fm-confirm-btn").disabled=false;document.getElementById("folder-modal").classList.add("show");}
function updateFolderTarget(){const name=document.getElementById("fm-root-name").value||"name";document.getElementById("fm-target-path").textContent=(_folderType==="car"?"/content/cars/":"/content/tracks/")+name+"/";}
function closeFolderModal(){document.getElementById("folder-modal").classList.remove("show");document.getElementById("folder-inp").value="";}
function uploadFolder(){
  const rootName=document.getElementById("fm-root-name").value.trim();if(!rootName||!_folderFiles)return;
  const files=Array.from(_folderFiles),total=files.length;
  const prog=document.getElementById("fm-upload-prog"),msg=document.getElementById("fm-up-msg"),pct=document.getElementById("fm-up-pct"),bar=document.getElementById("fm-up-bar");
  prog.style.display="block";document.getElementById("fm-confirm-btn").disabled=true;
  let done=0,failed=[];
  function uploadOne(file){return new Promise(resolve=>{const rel=(file.webkitRelativePath||file.name).split("/").slice(1).join("/")||file.name;const fd=new FormData();fd.append("type",_folderType);fd.append("root_name",rootName);fd.append("rel_path",rel);fd.append("file",file);const xhr=new XMLHttpRequest();xhr.open("POST","/upload_file");xhr.upload.onprogress=e=>{if(!e.lengthComputable)return;const overall=Math.round((done+e.loaded/e.total)/total*100);bar.style.width=overall+"%";pct.textContent=overall+"%";msg.textContent=`${done+1}/${total}: ${rel.split("/").pop()}`;};xhr.onload=()=>{done++;try{const d=JSON.parse(xhr.responseText);if(!d.ok)failed.push(rel);}catch(e){failed.push(rel);}bar.style.width=Math.round(done/total*100)+"%";pct.textContent=Math.round(done/total*100)+"%";resolve();};xhr.onerror=()=>{done++;failed.push(rel);resolve();};xhr.send(fd);});}
  (async()=>{for(const f of files)await uploadOne(f);const doneResp=await fetch("/upload_folder_done",{method:"POST",credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:_folderType,root_name:rootName})}).then(r=>r.json()).catch(()=>({ok:false}));prog.style.display="none";closeFolderModal();if(failed.length){toast(`⚠ ${failed.length} Datei(en) fehlgeschlagen`,"err");return;}if(confirm(`✓ ${rootName} importiert (${total} Dateien).\n\nServer jetzt neu starten?`)){apiFetch("/control/restart",{method:"POST"}).then(()=>toast("Server wird neu gestartet...","info"));}else{toast(`✓ ${esc(rootName)} importiert`,"ok");}setTimeout(()=>location.reload(),2500);})();
}

// ═══ UTILS ════════════════════════════════════════════════════════════════
function filterCars(){const q=document.getElementById("car-search").value.toLowerCase();document.querySelectorAll("#car-list .ci").forEach(el=>el.style.display=el.textContent.toLowerCase().includes(q)?"":"none");}
function filterZip(listId,searchId){const q=document.getElementById(searchId).value.toLowerCase();document.querySelectorAll(`#${listId} .ci`).forEach(el=>el.style.display=el.textContent.toLowerCase().includes(q)?"":"none");}
function fmt(ms){if(!ms||ms<=0)return"—";const m=Math.floor(ms/60000),s=Math.floor((ms%60000)/1000),ms3=ms%1000;return`${m}:${String(s).padStart(2,"0")}.${String(ms3).padStart(3,"0")}`;}
function esc(s){return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
function copy(text){navigator.clipboard.writeText(text).then(()=>toast("📋 Kopiert: "+text,"info"));}
function copyPub(){const ip=document.getElementById("pub-ip").textContent;if(ip&&ip!=="—")copy(ip+":9600");}

// ═══ QUICK STATS ══════════════════════════════════════════════════════════
function refreshQuickStats() {
  apiFetch('/api/laptimes/today').then(r=>r.json()).then(d=>{
    document.getElementById('qs-laps').textContent    = d.laps_today    ?? '0';
    document.getElementById('qs-drivers').textContent = d.drivers_today ?? '0';
    if (d.best_today) {
      document.getElementById('qs-best').textContent        = fmtMs(d.best_today.laptime);
      document.getElementById('qs-best-driver').textContent = '(' + esc(d.best_today.driver) + ')';
    } else {
      document.getElementById('qs-best').textContent        = '—';
      document.getElementById('qs-best-driver').textContent = '';
    }
  }).catch(()=>{});
}

// ═══ RECORDS TAB ══════════════════════════════════════════════════════════
function fmtMs(ms) {
  if (!ms || ms <= 0) return '—';
  const m = Math.floor(ms / 60000), s = (ms % 60000) / 1000;
  return `${m}:${s.toFixed(3).padStart(6,'0')}`;
}

// Fix: rebuild filter options without overwriting data-i18n on the "Alle" option
async function loadRecordFilters() {
  const d = await apiFetch('/api/laptimes/drivers').then(r => r.json()).catch(() => null);
  if (!d) return;
  const L = LANG[curLang] || LANG.de;
  const fill = (id, items, allKey) => {
    const sel = document.getElementById(id);
    const cur = sel.value;
    sel.innerHTML = `<option value="">${L[allKey] || 'Alle'}</option>` +
      items.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
    if (cur) sel.value = cur;
  };
  fill('rec-filter-driver', d.drivers, 'all_drivers');
  fill('rec-filter-track',  d.tracks,  'all_tracks');
  fill('rec-filter-car',    d.cars,    'all_cars');
}

async function loadBestLaps() {
  const d = await apiFetch('/api/laptimes/best').then(r => r.json()).catch(() => null);
  const tb = document.getElementById('best-tbody');
  if (!d || !d.entries.length) {
    tb.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:32px">${t('best_no_data')}</td></tr>`;
    return;
  }
  tb.innerHTML = d.entries.map((e, i) => {
    const pc = ['pos-1','pos-2','pos-3'][i] || '';
    const medal = ['🥇','🥈','🥉'][i] || `#${i+1}`;
    return `<tr>
      <td class="${pc}">${medal}</td>
      <td><strong>${esc(e.driver)}</strong></td>
      <td style="color:var(--muted);font-size:11px">${esc(e.car)}</td>
      <td style="font-size:11px">${esc(e.track)}</td>
      <td class="${pc} best-t" style="font-family:monospace;font-weight:800">${fmtMs(e.laptime)}</td>
      <td style="color:${e.cuts>0?'var(--yellow)':'var(--muted)'};">${e.cuts}</td>
      <td style="color:var(--muted);font-size:11px">${esc(e.ts||'')}</td>
    </tr>`;
  }).join('');
}

// Pagination state
let _allLapsData = [], _allLapsPage = 0;
const _PAGE_SIZE = 50;

async function loadAllLaps(resetPage) {
  if (resetPage !== false) _allLapsPage = 0;
  const driver = document.getElementById('rec-filter-driver').value;
  const track  = document.getElementById('rec-filter-track').value;
  const car    = document.getElementById('rec-filter-car').value;
  const params = new URLSearchParams();
  if (driver) params.set('driver', driver);
  if (track)  params.set('track',  track);
  if (car)    params.set('car',    car);

  const d = await apiFetch('/api/laptimes?' + params).then(r => r.json()).catch(() => null);
  const tb = document.getElementById('all-tbody');
  const cnt = document.getElementById('rec-count');
  if (!d || !d.entries.length) {
    tb.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:32px">${t('t_no_entries')}</td></tr>`;
    cnt.textContent = t('t_entries',0);
    document.getElementById('all-pagination').innerHTML = '';
    return;
  }
  _allLapsData = d.entries;
  cnt.textContent = t('t_entries',d.total);
  document.getElementById('rec-export-btn').href = '/api/laptimes/export?' + params;
  renderAllLapsPage();
}

function renderAllLapsPage() {
  const data = _allLapsData;
  const start = _allLapsPage * _PAGE_SIZE;
  const page  = data.slice(start, start + _PAGE_SIZE);
  const tb    = document.getElementById('all-tbody');

  tb.innerHTML = page.map((e, i) => {
    const gi = start + i;
    const pc = ['pos-1','pos-2','pos-3'][gi] || '';
    const guidShort = e.guid ? e.guid.slice(-8) : '—';
    return `<tr>
      <td class="${pc}">${gi+1}</td>
      <td><strong>${esc(e.driver)}</strong></td>
      <td style="font-size:10px;color:var(--muted);font-family:monospace" title="${esc(e.guid)}">${guidShort}</td>
      <td style="font-size:11px;color:var(--muted)">${esc(e.car)}</td>
      <td style="font-size:11px">${esc(e.track)}</td>
      <td class="${gi===0?'best-t':''}" style="font-family:monospace;font-weight:700">${fmtMs(e.laptime)}</td>
      <td style="color:${e.cuts>0?'var(--yellow)':'var(--muted)'};">${e.cuts}</td>
      <td style="color:var(--muted);font-size:11px">${esc(e.ts||'')}</td>
    </tr>`;
  }).join('');

  // Pagination controls
  const pages = Math.ceil(data.length / _PAGE_SIZE);
  const pag   = document.getElementById('all-pagination');
  if (pages <= 1) { pag.innerHTML = ''; return; }
  let html = '';
  if (_allLapsPage > 0)
    html += `<button class="btn btn-gray btn-sm" onclick="_allLapsPage--;renderAllLapsPage()">‹</button>`;
  const lo = Math.max(0, _allLapsPage-2), hi = Math.min(pages-1, _allLapsPage+2);
  for (let p = lo; p <= hi; p++)
    html += `<button class="btn ${p===_allLapsPage?'btn-red':'btn-gray'} btn-sm" onclick="_allLapsPage=${p};renderAllLapsPage()">${p+1}</button>`;
  if (_allLapsPage < pages-1)
    html += `<button class="btn btn-gray btn-sm" onclick="_allLapsPage++;renderAllLapsPage()">›</button>`;
  html += `<span style="font-size:11px;color:var(--muted)">Seite ${_allLapsPage+1}/${pages}</span>`;
  pag.innerHTML = html;
}

function clearLaptimes() {
  if (!confirm(t('t_laptimes_del_confirm'))) return;
  apiFetch('/api/laptimes', {method:'DELETE'}).then(r=>r.json()).then(d=>{
    toast(d.ok?t('t_laptimes_deleted'):'✗ '+d.msg, d.ok?'ok':'err');
    if (d.ok) { loadBestLaps(); loadAllLaps(); loadDriverStats(); refreshQuickStats(); }
  });
}

async function loadDriverStats() {
  const d = await apiFetch('/api/laptimes/stats').then(r=>r.json()).catch(()=>null);
  const tb = document.getElementById('stats-tbody');
  if (!d || !d.stats.length) {
    tb.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:32px">Keine Daten</td></tr>`;
    return;
  }
  tb.innerHTML = d.stats.map((s, i) => {
    const pc = ['pos-1','pos-2','pos-3'][i] || '';
    // Find best track entry
    const bestTrack = Object.entries(s.tracks).sort((a,b)=>((a[1].best||99999999)-(b[1].best||99999999)))[0];
    const cleanPct = s.total_laps ? Math.round(s.clean_laps/s.total_laps*100) : 0;
    return `<tr>
      <td class="${pc}"><strong>${esc(s.driver)}</strong><div style="font-size:10px;color:var(--muted);font-family:monospace">${esc(s.guid.slice(-8)||'')}</div></td>
      <td style="font-weight:700">${s.total_laps}</td>
      <td><span style="color:${cleanPct>=80?'var(--green)':cleanPct>=50?'var(--yellow)':'var(--red)'}">${s.clean_laps}</span> <span style="color:var(--muted);font-size:11px">(${cleanPct}%)</span></td>
      <td class="best-t" style="font-family:monospace">${fmtMs(s.best_overall)}</td>
      <td style="font-size:11px;color:var(--muted)">${bestTrack?esc(bestTrack[0]):'—'}</td>
      <td style="font-size:11px;color:var(--muted)">${bestTrack?esc(bestTrack[1].car||''):'—'}</td>
    </tr>`;
  }).join('');
}

// ═══ DISCORD ══════════════════════════════════════════════════════════════
function loadDiscord() {
  apiFetch('/api/discord').then(r=>r.json()).then(d=>{
    document.getElementById('discord-url').value = d.url||'';
    const cb = document.getElementById('discord-notify-join');
    if (cb) cb.checked = !!d.notify_join;
  }).catch(()=>{});
}
function saveDiscord() {
  const url  = document.getElementById('discord-url').value.trim();
  const nj   = document.getElementById('discord-notify-join')?.checked || false;
  apiFetch('/api/discord',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,notify_join:nj})})
    .then(r=>r.json()).then(d=>toast(d.ok?t('t_webhook_saved'):'✗ '+d.msg,d.ok?'ok':'err'));
}
function testDiscord(){apiFetch('/api/discord/test',{method:'POST'}).then(r=>r.json()).then(d=>toast(d.ok?'✓ '+d.msg:'✗ '+d.msg,d.ok?'ok':'err'));}

// ═══ TRACK PARAMS ═════════════════════════════════════════════════════════
function saveTrackParams() {
  const track = document.getElementById('tp-track').value;
  const lat   = parseFloat(document.getElementById('tp-lat').value)||0;
  const lon   = parseFloat(document.getElementById('tp-lon').value)||0;
  const tz    = parseInt(document.getElementById('tp-tz').value) || 1;
  apiFetch('/api/add_track_params',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({track,lat,lon,tz,city:track})})
    .then(r=>r.json()).then(d=>toast(d.ok?'✓ '+d.msg:'✗ '+d.msg,d.ok?'ok':'err'));
}

// ═══ NAVIGATION (SIDEBAR) ════════════════════════════════════════════════
function navTo(id) {
  document.querySelectorAll('.panel').forEach(p => { p.classList.remove('active'); p.style.display = ''; });
  const target = document.getElementById('p-' + id);
  if (target) { target.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelectorAll(`.nav-item[data-nav="${id}"]`).forEach(n => n.classList.add('active'));
  // Scroll content to top
  document.querySelector('.content').scrollTop = 0;
  // Side effects
  if (id === 'logs') loadLogs();
  if (id === 'settings-profile') loadServerProfile();
  if (id === 'players') { loadGuidList('whitelist'); loadGuidList('admins'); loadGuidList('blacklist'); }
  if (id === 'advanced') { loadExtraCfg(); loadDiscord(); }
  if (id === 'content') loadInstalledContent();
  if (id === 'settings-overview') loadOverviewExtraCfg();
  if (id === 'records') { loadRecordFilters(); loadBestLaps(); loadAllLaps(); loadDriverStats(); }
  if (id === 'events') loadEvents();
  // Close mobile sidebar
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sb-overlay').classList.remove('open');
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sb-overlay').classList.toggle('open');
}

// ═══ COMBINED SAVE FUNCTIONS ══════════════════════════════════════════════
function saveAllServerSettings() {
  const bool = id => document.getElementById(id).checked ? '1' : '0';
  const val  = id => document.getElementById(id).value;
  const data = {
    NAME: val('sv-name'),
    REGISTER_TO_LOBBY: bool('sv-lobby'),
    SUN_ANGLE: val('sv-sun'),
    UDP_PORT: val('sv-udp'), TCP_PORT: val('sv-udp'),
    HTTP_PORT: val('sv-http'),
    PICKUP_MODE_ENABLED: bool('sv-pickup'),
    LOOP_MODE: bool('sv-loop'),
    BLACKLIST_MODE: bool('sv-blacklist'),
    KICK_QUORUM: val('sv-kickq'),
    VOTING_QUORUM: val('sv-voteq'),
    VOTE_DURATION: val('sv-vdur'),
    RACE_OVER_TIME: val('sv-raceover'),
    CLIENT_SEND_INTERVAL_HZ: val('sv-hz'),
    LEGAL_TYRES: val('sv-tyres'),
    restart: autoRestart()
  };
  const pass = document.getElementById('sv-pass').value;
  if (pass) data.PASSWORD = pass;
  const ap = document.getElementById('sv-adminpass').value;
  if (ap) data.ADMIN_PASSWORD = ap;
  apiFetch('/save_server_settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})
    .then(r=>r.json()).then(d=>toast(d.ok?t('t_server_saved'):'✗ '+d.msg, d.ok?'ok':'err'));
}

function saveWeatherPage() {
  const wd = {
    weather_0_graphics: document.getElementById('w0-graphics').value,
    weather_0_ambient: document.getElementById('w0-amb').value,
    weather_0_road: document.getElementById('w0-road').value,
    weather_1_graphics: document.getElementById('w1-graphics').value,
    weather_1_ambient: document.getElementById('w1-amb').value,
    weather_1_road: document.getElementById('w1-road').value,
    restart: autoRestart()
  };
  const dd = {
    SESSION_START: document.getElementById('dt-start').value,
    RANDOMNESS: document.getElementById('dt-rand').value,
    SESSION_TRANSFER: document.getElementById('dt-transfer').value,
    LAP_GAIN: document.getElementById('dt-lap').value,
    restart: autoRestart()
  };
  Promise.all([
    apiFetch('/save_weather', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(wd)}).then(r=>r.json()),
    apiFetch('/save_dynamic_track', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(dd)}).then(r=>r.json())
  ]).then(([w, d]) => {
    toast((w.ok && d.ok) ? t('t_weather_saved') : '✗ '+t('t_server_error'), (w.ok && d.ok) ? 'ok' : 'err');
  });
}

// ═══ INIT ═════════════════════════════════════════════════════════════════
applyLang();
navTo('dashboard');
updateLayouts();
loadTrackPreview();
refreshLive();
loadEvents();
loadMapImage();
loadPresetList();
refreshUptime();
loadServerProfile();
refreshQuickStats();

setInterval(refreshLive, 3000);
setInterval(function _tickLapTimers() {
  const now = Date.now();
  for (const [id, b] of Object.entries(_lapBases)) {
    const ms = b.base + (now - b.t);
    const v = fmt(ms);
    const el1 = document.getElementById('ct-' + id);
    if (el1) el1.textContent = v;
    const el2 = document.getElementById('clt-' + id);
    if (el2) el2.textContent = v;
  }
}, 100);
setInterval(refreshUptime, 30000);
setInterval(refreshQuickStats, 60000);
setInterval(() => {
  if (document.getElementById('ev-auto')?.checked) loadEvents();
}, 15000);
setInterval(()=>{
  const logsPanel = document.getElementById('p-logs');
  if(document.getElementById('log-auto').checked && logsPanel && logsPanel.classList.contains('active'))
    loadLogs();
}, 3000);

// =============================================================================
// ENTRY LIST EDITOR
// =============================================================================

let _elSlots   = [];
let _elCars    = [];
let _elSkinMap = {};
let _elDragIdx = -1;
let _elSel     = new Set();
let _elMaxClients = 0;

// -- Load ---------------------------------------------------------------------

function loadEntryList() {
  apiFetch('/api/entry_list').then(r => r.json()).then(d => {
    if (!d.ok) { toast('Entry List Fehler: ' + d.msg, 'err'); return; }
    _elSlots      = d.slots || [];
    _elCars       = d.cars  || [];
    _elMaxClients = d.max_clients || 0;
    _elSkinMap    = {};
    _elSel.clear();
    _elPopulateQACar();
    elRender();
  }).catch(() => toast('Entry List laden fehlgeschlagen', 'err'));
}

// -- Quick-Add car dropdown ---------------------------------------------------

function _elPopulateQACar() {
  const sel = document.getElementById('el-qa-car');
  if (!sel) return;
  sel.innerHTML = _elCars.map(c => '<option value="' + esc(c) + '">' + esc(c) + '</option>').join('');
  elQACarChange();
}

function elQACarChange() {
  const car = document.getElementById('el-qa-car') && document.getElementById('el-qa-car').value;
  if (!car) return;
  _elLoadSkins(car).then(skins => {
    const sel = document.getElementById('el-qa-skin');
    if (!sel) return;
    sel.innerHTML = skins.map(s => '<option value="' + esc(s.name) + '">' + esc(s.name) + '</option>').join('');
  });
}

// -- Skin loading (cached) ----------------------------------------------------

function _elLoadSkins(car) {
  if (_elSkinMap[car]) return Promise.resolve(_elSkinMap[car]);
  return apiFetch('/api/car_skins_detail/' + encodeURIComponent(car))
    .then(r => r.json())
    .then(d => { _elSkinMap[car] = d.skins || []; return _elSkinMap[car]; })
    .catch(() => []);
}

// -- Render -------------------------------------------------------------------

function elRender() {
  const grid   = document.getElementById('el-grid');
  const empty  = document.getElementById('el-empty');
  const count  = document.getElementById('el-slot-count');
  const mcInfo = document.getElementById('el-maxclients-info');
  if (!grid) return;

  if (count) count.textContent = _elSlots.length;
  if (mcInfo) {
    if (_elMaxClients > 0) {
      const over = _elSlots.length > _elMaxClients;
      mcInfo.innerHTML = 'MAX_CLIENTS: <strong style="color:' + (over ? 'var(--red)' : 'inherit') + '">' + _elMaxClients + '</strong>';
    } else {
      mcInfo.textContent = '';
    }
  }

  if (_elSlots.length === 0) {
    if (empty) empty.style.display = 'block';
    [...grid.children].forEach(c => { if (!c.classList.contains('el-empty')) c.remove(); });
    elUpdateMultiBar();
    elValidate();
    return;
  }
  if (empty) empty.style.display = 'none';

  const frag = document.createDocumentFragment();
  _elSlots.forEach((slot, i) => frag.appendChild(_elBuildCard(slot, i)));
  [...grid.children].forEach(c => { if (!c.classList.contains('el-empty')) c.remove(); });
  grid.appendChild(frag);

  _elSlots.forEach((slot, i) => { if (slot.model) _elPopulateSkinSel(i, slot.model, slot.skin); });

  elUpdateMultiBar();
  elValidate();
}

// -- Build one slot card ------------------------------------------------------

function _elBuildCard(slot, i) {
  const div = document.createElement('div');
  div.className = 'el-slot' + (_elSel.has(i) ? ' el-selected' : '');
  div.dataset.index = i;
  div.draggable = true;

  div.addEventListener('dragstart', e => {
    _elDragIdx = i;
    div.classList.add('el-dragging');
    e.dataTransfer.effectAllowed = 'move';
  });
  div.addEventListener('dragend', () => {
    div.classList.remove('el-dragging');
    document.querySelectorAll('.el-drag-over').forEach(el => el.classList.remove('el-drag-over'));
  });
  div.addEventListener('dragover', e => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    document.querySelectorAll('.el-drag-over').forEach(el => el.classList.remove('el-drag-over'));
    div.classList.add('el-drag-over');
  });
  div.addEventListener('drop', e => {
    e.preventDefault();
    div.classList.remove('el-drag-over');
    if (_elDragIdx === -1 || _elDragIdx === i) return;
    const moved  = _elSlots.splice(_elDragIdx, 1)[0];
    const target = _elDragIdx < i ? i - 1 : i;
    _elSlots.splice(target, 0, moved);
    _elSel.clear();
    elRender();
  });

  const thumbSrc     = slot.model ? '/car_img/'  + encodeURIComponent(slot.model) : '';
  const skinThumbSrc = (slot.model && slot.skin)
    ? '/skin_img/' + encodeURIComponent(slot.model) + '/' + encodeURIComponent(slot.skin) : '';

  const carOptions = ['<option value="">— Auto w\xe4hlen —</option>']
    .concat(_elCars.map(c => '<option value="' + esc(c) + '"' + (c === slot.model ? ' selected' : '') + '>' + esc(c) + '</option>'))
    .join('');

  div.innerHTML =
    '<div class="el-handle" title="Ziehen zum Umsortieren">⠇</div>' +
    '<div class="el-slot-cb"><input type="checkbox" ' + (_elSel.has(i) ? 'checked' : '') + ' onchange="elToggleSel(' + i + ',this.checked)"></div>' +
    '<div class="el-car-col">' +
      '<img class="el-car-thumb" src="' + thumbSrc + '" alt="" onerror="this.style.display=\'none\'">' +
      '<select class="sel el-car-sel" data-idx="' + i + '" onchange="elCarChange(' + i + ',this.value)">' + carOptions + '</select>' +
    '</div>' +
    '<div class="el-skin-col">' +
      '<img class="el-skin-thumb" id="el-sthumb-' + i + '" src="' + skinThumbSrc + '" alt="" onerror="this.style.opacity=\'0\'" style="opacity:' + (skinThumbSrc ? '1' : '0') + '">' +
      '<select class="sel el-skin-sel" id="el-ssel-' + i + '" data-idx="' + i + '" onchange="elSkinChange(' + i + ',this.value)">' +
        '<option value="' + esc(slot.skin || '') + '">' + esc(slot.skin || '— l\xe4dt… —') + '</option>' +
      '</select>' +
    '</div>' +
    '<div class="el-num-col"><label>Ballast kg</label><input class="inp num-inp" type="number" min="0" max="150" value="' + (slot.ballast || 0) + '" onchange="elUpd(' + i + ',\'ballast\',+this.value)" style="width:100%"></div>' +
    '<div class="el-num-col"><label>Restrictor %</label><input class="inp num-inp" type="number" min="0" max="400" value="' + (slot.restrictor || 0) + '" onchange="elUpd(' + i + ',\'restrictor\',+this.value)" style="width:100%"></div>' +
    '<div class="el-driver-col"><label>Fahrername</label><input class="inp" type="text" value="' + esc(slot.drivername || '') + '" placeholder="leer = offen" onchange="elUpd(' + i + ',\'drivername\',this.value)" style="width:100%;font-size:11px"></div>' +
    '<div class="el-guid-col"><label>Steam GUID</label><input class="inp" type="text" value="' + esc(slot.guid || '') + '" placeholder="leer = offen" onchange="elUpd(' + i + ',\'guid\',this.value)" style="width:100%;font-size:11px"></div>' +
    '<div class="el-slot-actions"><span class="el-slot-num">#' + (i + 1) + '</span>' +
      '<button class="btn btn-gray btn-sm" onclick="elDuplicateSlot(' + i + ')" title="Duplizieren">⊕</button>' +
      '<button class="btn btn-gray btn-sm" onclick="elDeleteSlot(' + i + ')" title="L\xf6schen">🗑</button>' +
    '</div>';
  return div;
}

// -- Populate skin select asynchronously --------------------------------------

function _elPopulateSkinSel(idx, car, currentSkin) {
  _elLoadSkins(car).then(skins => {
    const sel   = document.getElementById('el-ssel-' + idx);
    const thumb = document.getElementById('el-sthumb-' + idx);
    if (!sel) return;
    sel.innerHTML = skins.map(s =>
      '<option value="' + esc(s.name) + '"' + (s.name === currentSkin ? ' selected' : '') + '>' + esc(s.name) + '</option>'
    ).join('');
    if (!currentSkin && skins.length) {
      sel.value = skins[0].name;
      if (_elSlots[idx]) _elSlots[idx].skin = skins[0].name;
    }
    const skinNow = sel.value;
    if (thumb && skinNow) {
      thumb.src = '/skin_img/' + encodeURIComponent(car) + '/' + encodeURIComponent(skinNow);
      thumb.style.opacity = '1';
    }
  });
}

// -- CRUD helpers -------------------------------------------------------------

function elUpd(idx, field, val) {
  if (!_elSlots[idx]) return;
  _elSlots[idx][field] = val;
  elValidate();
}

function elCarChange(idx, car) {
  if (!_elSlots[idx]) return;
  _elSlots[idx].model = car;
  _elSlots[idx].skin  = '';
  const thumb = document.querySelector('.el-slot[data-index="' + idx + '"] .el-car-thumb');
  if (thumb) { thumb.src = car ? '/car_img/' + encodeURIComponent(car) : ''; thumb.style.display = car ? '' : 'none'; }
  _elPopulateSkinSel(idx, car, '');
  elValidate();
}

function elSkinChange(idx, skin) {
  if (!_elSlots[idx]) return;
  _elSlots[idx].skin = skin;
  const car   = _elSlots[idx].model;
  const thumb = document.getElementById('el-sthumb-' + idx);
  if (thumb && car && skin) {
    thumb.src = '/skin_img/' + encodeURIComponent(car) + '/' + encodeURIComponent(skin);
    thumb.style.opacity = '1';
  }
  elValidate();
}

function elDeleteSlot(idx) {
  _elSlots.splice(idx, 1);
  _elSel.clear();
  elRender();
}

function elDuplicateSlot(idx) {
  const clone = Object.assign({}, _elSlots[idx]);
  _elSlots.splice(idx + 1, 0, clone);
  _elSel.clear();
  elRender();
}

// -- Quick Add ----------------------------------------------------------------

function elQuickAdd() {
  const car  = (document.getElementById('el-qa-car')  || {}).value || '';
  const skin = (document.getElementById('el-qa-skin') || {}).value || '';
  const n    = Math.min(20, Math.max(1, parseInt((document.getElementById('el-qa-count') || {}).value || '1')));
  for (let i = 0; i < n; i++) {
    _elSlots.push({ model: car, skin: skin, ballast: 0, restrictor: 0, drivername: '', team: '', guid: '', spectator: 0 });
  }
  _elSel.clear();
  elRender();
}

// -- Multi-edit ---------------------------------------------------------------

function elToggleSel(idx, checked) {
  if (checked) _elSel.add(idx); else _elSel.delete(idx);
  const card = document.querySelector('.el-slot[data-index="' + idx + '"]');
  if (card) card.classList.toggle('el-selected', checked);
  elUpdateMultiBar();
}

function elUpdateMultiBar() {
  const bar = document.getElementById('el-multiedit');
  const cnt = document.getElementById('el-sel-count');
  if (!bar) return;
  if (_elSel.size === 0) {
    bar.style.display = 'none';
  } else {
    bar.style.display = 'flex';
    if (cnt) cnt.textContent = _elSel.size + ' ausgew\xe4hlt';
  }
}

function elClearSelection() {
  _elSel.clear();
  document.querySelectorAll('.el-slot.el-selected').forEach(el => el.classList.remove('el-selected'));
  document.querySelectorAll('.el-slot input[type=checkbox]').forEach(cb => { cb.checked = false; });
  elUpdateMultiBar();
}

function elMultiApply() {
  const bv = (document.getElementById('el-me-ballast')    || {}).value;
  const rv = (document.getElementById('el-me-restrictor') || {}).value;
  _elSel.forEach(idx => {
    if (!_elSlots[idx]) return;
    if (bv !== undefined && bv !== '') _elSlots[idx].ballast    = Math.max(0, Math.min(150, parseInt(bv) || 0));
    if (rv !== undefined && rv !== '') _elSlots[idx].restrictor = Math.max(0, Math.min(400, parseInt(rv) || 0));
  });
  _elSel.clear();
  elRender();
}

function elMultiDelete() {
  if (!confirm(_elSel.size + ' Slot(s) wirklich l\xf6schen?')) return;
  _elSlots = _elSlots.filter((_, i) => !_elSel.has(i));
  _elSel.clear();
  elRender();
}

// -- Validation ---------------------------------------------------------------

function elValidate() {
  const banner   = document.getElementById('el-banner');
  const warnings = [];
  const errors   = [];

  const guidCount = {};
  _elSlots.forEach((s, i) => {
    const g = (s.guid || '').trim();
    if (g) {
      if (!guidCount[g]) guidCount[g] = [];
      guidCount[g].push(i);
    }
  });
  Object.keys(guidCount).forEach(g => {
    if (guidCount[g].length > 1)
      errors.push('Doppelte GUID "' + g + '" in Slots: ' + guidCount[g].map(x => x + 1).join(', '));
  });

  const emptyModel = _elSlots.filter(s => !s.model).length;
  if (emptyModel) warnings.push(emptyModel + ' Slot(s) ohne Auto-Auswahl');

  if (_elMaxClients > 0 && _elSlots.length > _elMaxClients) {
    warnings.push(_elSlots.length + ' Slots aber MAX_CLIENTS=' + _elMaxClients + ' — wird beim Speichern angepasst');
  }

  if (!banner) return;
  if (errors.length) {
    banner.className = 'el-banner err';
    banner.innerHTML = errors.map(e => '⚠ ' + e).join('<br>');
    banner.style.display = 'block';
  } else if (warnings.length) {
    banner.className = 'el-banner warn';
    banner.innerHTML = warnings.map(w => 'ℹ ' + w).join('<br>');
    banner.style.display = 'block';
  } else {
    banner.style.display = 'none';
  }
}

// -- Save ---------------------------------------------------------------------

function elSave() {
  apiFetch('/api/entry_list', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ slots: _elSlots }),
  }).then(r => r.json()).then(d => {
    toast(d.ok ? '✓ ' + d.msg : '✗ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) { _elMaxClients = d.total || _elMaxClients; elValidate(); }
  }).catch(() => toast('Speichern fehlgeschlagen', 'err'));
}

// -- Import INI ---------------------------------------------------------------

function elImportClick() {
  const f = document.getElementById('el-import-file');
  if (f) f.click();
}

function elImportFile(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    const slots = _elParseIni(e.target.result);
    if (!slots.length) { toast('Keine Slots in der Datei gefunden', 'err'); return; }
    if (!confirm(slots.length + ' Slots importieren? Aktuelle Liste wird ersetzt.')) return;
    _elSlots = slots;
    _elSel.clear();
    elRender();
    toast('✓ ' + slots.length + ' Slots importiert', 'ok');
  };
  reader.readAsText(file, 'utf-8');
  input.value = '';
}

function _elParseIni(text) {
  const slots = [];
  let cur = null;
  text.split('\n').forEach(raw => {
    const line = raw.trim();
    if (/^\[car_\d+\]$/i.test(line)) {
      if (cur) slots.push(cur);
      cur = { model:'', skin:'', ballast:0, restrictor:0, drivername:'', team:'', guid:'', spectator:0 };
    } else if (cur && line.includes('=')) {
      const eqIdx = line.indexOf('=');
      const key   = line.slice(0, eqIdx).trim().toUpperCase();
      const val   = line.slice(eqIdx + 1).trim();
      if      (key === 'MODEL')          cur.model       = val;
      else if (key === 'SKIN')           cur.skin        = val;
      else if (key === 'BALLAST')        cur.ballast      = parseInt(val) || 0;
      else if (key === 'RESTRICTOR')     cur.restrictor   = parseInt(val) || 0;
      else if (key === 'DRIVERNAME')     cur.drivername   = val;
      else if (key === 'TEAM')           cur.team         = val;
      else if (key === 'GUID')           cur.guid         = val;
      else if (key === 'SPECTATOR_MODE') cur.spectator    = parseInt(val) || 0;
    }
  });
  if (cur) slots.push(cur);
  return slots;
}

// -- Presets ------------------------------------------------------------------

function elLoadElPresets() {
  apiFetch('/api/entry_list_presets').then(r => r.json()).then(d => {
    const sel = document.getElementById('el-preset-sel');
    if (!sel) return;
    let opts = '<option value="">Preset laden…</option>';
    Object.keys(d.presets || {}).forEach(name => {
      const p = d.presets[name];
      opts += '<option value="' + esc(name) + '">' + esc(name) + ' (' + p.count + ' Slots, ' + p.saved + ')</option>';
    });
    sel.innerHTML = opts;
  }).catch(() => {});
}

function elLoadPreset() {
  const name = (document.getElementById('el-preset-sel') || {}).value;
  if (!name) return;
  apiFetch('/api/entry_list_presets').then(r => r.json()).then(d => {
    const preset = (d.presets || {})[name];
    if (!preset) { toast('Preset nicht gefunden', 'err'); return; }
    if (!confirm('Preset "' + name + '" laden? Aktuelle Liste wird ersetzt.')) return;
    _elSlots = preset.slots || [];
    _elSel.clear();
    elRender();
    toast('✓ Preset "' + name + '" geladen', 'ok');
  });
}

function elSavePreset() {
  const name = prompt('Preset-Name:');
  if (!name) return;
  apiFetch('/api/entry_list_presets', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ name: name, slots: _elSlots }),
  }).then(r => r.json()).then(d => {
    toast(d.ok ? '✓ ' + d.msg : '✗ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) elLoadElPresets();
  });
}

// -- Init: load when panel becomes active ------------------------------------

(function() {
  const obs = new MutationObserver(mutations => {
    mutations.forEach(m => {
      if (m.target.id === 'p-entry-list' && m.target.classList.contains('active')) {
        loadEntryList();
        elLoadElPresets();
      }
    });
  });
  document.addEventListener('DOMContentLoaded', () => {
    const panel = document.getElementById('p-entry-list');
    if (panel) obs.observe(panel, { attributeFilter: ['class'] });
    const qaCar = document.getElementById('el-qa-car');
    if (qaCar) qaCar.addEventListener('change', elQACarChange);
  });
})();