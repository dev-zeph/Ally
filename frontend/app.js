const TIER_LABEL = { watching: "Watching", check_soon: "Check soon", check_now: "Check now" };
const SOURCE_LABEL = { camera: "camera", wearable: "wearable (sim)", companion: "companion", system: "alley" };

const $ = (id) => document.getElementById(id);
const cardEls = new Map();

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function waited(min) {
  const h = Math.floor(min / 60);
  return h ? `${h}h ${min % 60}m` : `${min}m`;
}

function sparkline(values, base) {
  if (values.length < 2) return "";
  const w = 120, h = 28;
  const lo = Math.min(...values, base) - 4, hi = Math.max(...values, base) + 4;
  const y = (v) => (h - ((v - lo) / (hi - lo)) * h).toFixed(1);
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${y(v)}`).join(" ");
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-label="Heart rate trend">
    <line x1="0" x2="${w}" y1="${y(base)}" y2="${y(base)}" class="base"/>
    <polyline points="${pts}"/></svg>`;
}

function cardInner(p) {
  const seat = p.seat ? `Seat ${p.seat}` : "Off camera";
  const elevated = p.tier !== "watching";
  return `
    <div class="top">
      <div class="who">${esc(p.id)}<small>${esc(seat)} · ${esc(p.age)}${esc(p.sex)} · CTAS ${esc(p.ctas)}</small></div>
      <div class="tier">${esc(TIER_LABEL[p.tier] || p.tier)}${p.acknowledged ? " · acknowledged" : ""}</div>
    </div>
    <div class="complaint">${esc(p.complaint)}</div>
    <div class="row">
      <span>Waited ${waited(p.waited_min)}</span>
      ${sparkline(p.hr_history, p.triage_vitals.hr)}
      <span class="vitals">HR <b>${esc(p.hr)}</b> · SpO2 <b>${esc(p.spo2)}%</b></span>
      <span class="chip sim">Simulated</span>
      ${p.camera_state ? `<span class="chip cam ${esc(p.camera_state)}">${esc(p.camera_state)}</span>` : ""}
      ${p.corroborated ? `<span class="chip corroborated">Corroborated</span>` : ""}
    </div>
    ${p.reason ? `<div class="reason">${esc(p.reason)}</div>` : ""}
    <div class="actions">
      <button data-action="acknowledge" ${elevated && !p.acknowledged ? "" : "disabled"}>Acknowledge</button>
      <button data-action="clear" ${elevated ? "" : "disabled"}>Reassessed, clear</button>
      <button data-action="called_in">Called in</button>
    </div>`;
}

// Cards are kept and updated in place so the pulse animation doesn't restart every second.
function renderCards(patients) {
  const cards = $("cards");
  const ids = patients.map((p) => p.id);
  for (const [id, el] of cardEls) {
    if (!ids.includes(id)) { el.remove(); cardEls.delete(id); }
  }
  for (const p of patients) {
    let el = cardEls.get(p.id);
    if (!el) {
      el = document.createElement("div");
      el.dataset.id = p.id;
      cardEls.set(p.id, el);
    }
    const cls = `card ${p.tier}`;
    if (el.className !== cls) el.className = cls;
    el.innerHTML = cardInner(p);
  }
  const current = [...cards.children].map((c) => c.dataset.id);
  if (current.join() !== ids.join()) ids.forEach((id) => cards.appendChild(cardEls.get(id)));
}

function eventRow(e) {
  return `
    <li class="${esc(e.severity)}">
      <span class="ts">${esc(e.ts.slice(11, 19))}</span>
      <span class="src">${esc(SOURCE_LABEL[e.source] || e.source)}</span>
      <span class="detail">${esc(e.patient_id)}: ${esc(e.detail)}</span>
    </li>`;
}

function render(state) {
  $("clock").textContent = state.now.slice(11, 19);
  $("mode").textContent = `${state.mode} mode`;
  $("count").textContent = `${state.patients.length} waiting`;
  renderCards(state.patients);
  $("events").innerHTML = state.events.length
    ? state.events.slice().reverse().map(eventRow).join("")
    : `<li class="empty">No events yet</li>`;

  const video = $("video");
  const live = state.camera === "live" || state.camera === "starting";
  if (live && !video.dataset.on) {
    video.dataset.on = "1";
    video.src = "/video";
    video.style.display = "block";
    $("video-placeholder").style.display = "none";
  }
  if (!live) {
    $("video-placeholder").textContent =
      state.camera === "test" ? "Camera off (test mode)" : "Camera not available";
  }
  $("cam-chip").textContent = state.camera === "live" ? "LIVE" : state.camera;
  $("cam-chip").className = `chip ${state.camera === "live" ? "live" : "off"}`;
}

async function act(id, action) {
  const res = await fetch(`/api/patients/${encodeURIComponent(id)}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const data = await res.json();
  if (data.handoff) {
    $("handoff-title").textContent = `Handoff: ${id}`;
    $("handoff-text").textContent = data.handoff;
    $("handoff").showModal();
  }
}

// pointerdown, not click: cards re-render every second and a click can straddle a re-render.
$("cards").addEventListener("pointerdown", (ev) => {
  const btn = ev.target.closest("button[data-action]");
  if (!btn || btn.disabled) return;
  act(btn.closest(".card").dataset.id, btn.dataset.action);
});

function setConn(on) {
  $("conn").textContent = on ? "connected" : "reconnecting";
  $("conn").className = `chip ${on ? "on" : "off"}`;
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onmessage = (msg) => render(JSON.parse(msg.data));
  ws.onclose = () => { setConn(false); setTimeout(connect, 1000); };
}

connect();
