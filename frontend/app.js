// ALLEY nurse dashboard. Plain HTML + vanilla JS, re-rendered from every WebSocket message.

const TIER_LABEL = { watching: "Watching", check_soon: "Check soon", check_now: "Check now" };
const TIER_DO = {
  check_now: "Go to this patient now and reassess.",
  check_soon: "Reassess this patient at the next opportunity.",
};
const CTAS_LABEL = { 1: "resuscitation", 2: "emergent", 3: "urgent", 4: "less urgent", 5: "non-urgent" };
const CAMERA_LABEL = {
  upright: "Sitting upright", slumped: "Slumped", lying: "Lying down", fallen: "Possible fall", empty: "Seat empty",
};
const SOURCE_LABEL = { camera: "Camera", wearable: "Wearable (simulated)", companion: "Companion", system: "ALLEY" };
const FLAG_LABEL = {
  recent_trauma: "recent injury", cardiac_history: "heart condition", anticoagulant: "on blood thinners",
};
const EVENT_TEXT = {
  head_drop: "Slumped in seat",
  lying: "Lying down across seats",
  fall: "Possible fall",
  seat_empty: "Seat empty",
  recovered: "Sitting upright again",
  acknowledged: "Nurse acknowledged",
  cleared: "Nurse reassessed and cleared",
  called_in: "Called in. Handoff written.",
};

const $ = (id) => document.getElementById(id);
const cardEls = new Map();
let latest = null;
let names = {};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// Server text into nurse language: no arrows, no abbreviations, no em dashes.
function clean(text) {
  return String(text ?? "")
    .replace(/\s*\((wearable|camera|companion|system)\)\s*$/i, "")
    .replace(/\s*(→|->)\s*/g, " to ")
    .replace(/\s*[—–]\s*/g, ", ")
    .replace(/\bHx:\s*/g, "History: ")
    .replace(/\bCTAS (\d)\b/g, (m, n) => `Triage level ${n}${CTAS_LABEL[n] ? ` (${CTAS_LABEL[n]})` : ""}`)
    .replace(/\bHR\b/g, "Heart rate")
    .replace(/\bSpO2\b/gi, "Oxygen")
    .replace(/~\s*/g, "about ")
    .replace(/^Escalated to/i, "Raised to")
    .replace(/\bCHECK NOW\b/g, "Check now").replace(/\bCHECK SOON\b/g, "Check soon").replace(/\bWATCHING\b/g, "Watching")
    .replace(/^Corroborated by (.*)$/i, (m, s) => `Two signals agree: ${s.replace(/\s*\+\s*/g, " and ")}`)
    .replace(/\(corroborated\)/i, "(two signals agree)")
    .replace(/Called in; handoff written/i, "Called in. Handoff written.")
    .trim();
}

function lowerFirst(s) { return s ? s.charAt(0).toLowerCase() + s.slice(1) : s; }
function displayName(p) { return p.name || `Patient ${p.id}`; }
function ctasText(n) { return `Triage level ${n}${CTAS_LABEL[n] ? ` (${CTAS_LABEL[n]})` : ""}`; }

function where(p) {
  if (p.side === "on camera") return "on camera";
  if (p.side) return `${p.side} seat`;
  if (p.seat) return `seat ${p.seat}`;
  return "waiting area, not on camera";
}

function waited(min) {
  if (min == null) return "";
  const h = Math.floor(min / 60);
  return h ? `${h}h ${min % 60}m` : `${min}m`;
}

function triage(p) { return p.triage_vitals || {}; }

function hrLine(p) {
  const base = triage(p).hr;
  if (!base || p.hr == null) return null;
  const pct = Math.round(((p.hr - base) / base) * 100);
  if (pct > 0) return `Heart rate up ${pct}% since triage (${base} to ${p.hr})`;
  return `Heart rate ${p.hr}`;
}

function spo2Line(p) {
  const base = triage(p).spo2;
  if (p.spo2 == null) return null;
  return base && p.spo2 < base
    ? `Oxygen level down to ${p.spo2}% (${base}% at triage)`
    : `Oxygen level ${p.spo2}%`;
}

function signalText(kind, detail, p) {
  const d = String(detail ?? "");
  if (kind === "head_drop" || /head dropping|slumped/i.test(d)) return "Slumped in seat";
  if (kind === "lying" || /^lying/i.test(d)) return "Lying down across seats";
  if (kind === "fall" || /possible fall/i.test(d)) return "Possible fall";
  if (kind === "seat_empty") return "Seat empty";
  if (kind === "hr_rise" || kind === "hr_high" || /^HR\b/.test(d)) return hrLine(p) || clean(d);
  if (kind === "spo2_low" || kind === "spo2_drop" || /^SpO2\b/i.test(d)) return spo2Line(p) || clean(d);
  return clean(d);
}

function historyText(p) {
  if (p.history_note) return `History: ${lowerFirst(clean(p.history_note))}`;
  const flags = (p.history_flags || []).map((f) => FLAG_LABEL[f] || String(f).replace(/_/g, " "));
  return flags.length ? `History: ${flags.join(", ")}` : "";
}

// The plain-language "why" lines for an elevated patient.
function whyLines(p) {
  const lines = [];
  const add = (t) => { if (t && !lines.includes(t)) lines.push(t); };
  if (Array.isArray(p.signals) && p.signals.length) {
    for (const s of p.signals) add(signalText(s.kind, s.detail, p));
  } else if (p.reason) {
    for (const part of String(p.reason).split(" · ")) {
      if (/^Hx:/i.test(part.trim())) continue;
      add(signalText("", part.replace(/\s*\((wearable|camera|companion|system)\)\s*$/i, "").trim(), p));
    }
  }
  if (p.tier !== "watching") add(historyText(p));
  return lines;
}

// The LLM sentence is worth showing; the server's mechanical fallback ("... (camera); ... (wearable)")
// only repeats the Why line in jargon, so it is skipped.
function noteText(p) {
  const note = String(p.alert_note || "").trim();
  if (!note || /\((camera|wearable|companion|system)\)/i.test(note)) return "";
  return clean(note);
}

function sentence(lines) { return lines.length ? `${lines.join(". ")}.` : ""; }

function activeKinds(p) {
  const kinds = (Array.isArray(p.signals) ? p.signals : []).map((s) => s.kind).join(" ");
  return `${kinds} ${p.reason || ""}`;
}

function sparkline(values, base) {
  if (!Array.isArray(values) || values.length < 2) return "";
  const w = 120, h = 34;
  const ref = base ?? values[0];
  const lo = Math.min(...values, ref) - 4, hi = Math.max(...values, ref) + 4;
  const y = (v) => (h - ((v - lo) / (hi - lo)) * h).toFixed(1);
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${y(v)}`).join(" ");
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-label="Heart rate trend">
    <line x1="0" x2="${w}" y1="${y(ref)}" y2="${y(ref)}" class="base"/>
    <polyline points="${pts}"/></svg>`;
}

/* ---------- Alert banner (updated in place) ---------- */

function setText(id, text) {
  const el = $(id);
  if (el.textContent !== text) el.textContent = text;
}

function setClass(el, cls) { if (el.className !== cls) el.className = cls; }

function renderAlert(patients) {
  const alert = $("alert");
  const now = patients.filter((p) => p.tier === "check_now");
  const soon = patients.filter((p) => p.tier === "check_soon");

  if (!now.length) {
    const n = patients.length;
    if (soon.length) {
      const p = soon[0];
      setClass(alert, "soon");
      setText("calm-text", `Check soon: ${displayName(p)}, ${where(p)}${soon.length > 1 ? `, and ${soon.length - 1} more` : ""}. ${TIER_DO.check_soon}`);
      setText("calm-sub", `ALLEY is watching ${n} ${n === 1 ? "person" : "people"}.`);
    } else {
      setClass(alert, "calm");
      setText("calm-text", n ? `No alerts right now. ALLEY is watching ${n} ${n === 1 ? "person" : "people"}.` : "No patients waiting.");
      setText("calm-sub", "No one needs checking right now.");
    }
    alert.dataset.id = "";
    return;
  }

  // Top-ranked patient that nobody has picked up yet; otherwise the top-ranked one.
  const top = now.find((p) => !p.acknowledged) || now[0];
  const acked = !!top.acknowledged;
  setClass(alert, `now${acked ? " acked" : ""}`);
  alert.dataset.id = top.id;

  setText("now-kicker", acked ? "Acknowledged. A nurse is on it." : "Needs a nurse now");
  setText("now-head", `CHECK NOW: ${displayName(top)}, ${where(top)}`);
  setText("now-note", noteText(top));
  setText("now-why", sentence(whyLines(top)) || "Signs logged by ALLEY.");
  setText("now-do", TIER_DO.check_now);
  const meta = [
    top.ctas != null ? ctasText(top.ctas) : "",
    top.complaint ? `Came in with ${top.complaint}` : "",
    top.waited_min != null ? `Waiting ${waited(top.waited_min)}` : "",
    top.corroborated ? "Two signals agree" : "",
    "Wearable values are simulated",
  ].filter(Boolean).join(". ");
  setText("now-meta", `${meta}.`);

  const ack = $("now-ack");
  setText("now-ack", acked ? "Reassessed, clear" : "I'm on it");
  ack.dataset.action = acked ? "clear" : "acknowledge";

  const others = now.filter((p) => p !== top);
  const html = others.map((p) => `
    <div class="other" data-id="${esc(p.id)}">
      <span class="o-who">${esc(displayName(p))}, ${esc(where(p))}</span>
      <span class="o-why">${esc(sentence(whyLines(p)))}${p.acknowledged ? " Acknowledged." : ""}</span>
      ${p.acknowledged
        ? `<button data-action="clear">Reassessed, clear</button>`
        : `<button data-action="acknowledge">I'm on it</button>`}
      <button class="ghost" data-action="called_in">Called in</button>
    </div>`).join("");
  const box = $("alert-others");
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
}

/* ---------- Patient list ---------- */

function fullCard(p) {
  const elevated = p.tier !== "watching";
  const kinds = activeKinds(p);
  const flag = p.tier === "check_now" ? "bad" : "warn";
  const hrFlag = elevated && /hr_|HR /.test(kinds) ? flag : "";
  const o2Flag = elevated && /spo2/i.test(kinds) ? flag : "";
  const camFlag = p.camera_state === "slumped" ? "warn" : (p.camera_state === "lying" || p.camera_state === "fallen") ? "bad" : "";
  const base = triage(p);
  const lines = elevated ? whyLines(p) : [];
  const sub = [
    p.age != null ? `${p.age} ${p.sex || ""}`.trim() : "",
    p.ctas != null ? ctasText(p.ctas) : "",
    p.waited_min != null ? `Waiting ${waited(p.waited_min)}` : "",
  ].filter(Boolean).map(esc).join(" · ");

  return `
    <div class="top">
      <div class="who">${esc(displayName(p))}<small>${esc(where(p))}${p.name ? ` · ${esc(p.id)}` : ""}</small></div>
      <div class="tier">${esc(TIER_LABEL[p.tier] || p.tier)}${p.acknowledged ? ", acknowledged" : ""}</div>
    </div>
    <div class="sub">${sub}${p.complaint ? ` · Came in with <b>${esc(p.complaint)}</b>` : ""}</div>
    <div class="facts">
      <div class="fact ${hrFlag}">
        <div class="k">Heart rate <span class="chip sim">Simulated</span></div>
        <div class="v">${esc(p.hr ?? "?")}<small>${base.hr != null ? `was ${esc(base.hr)} at triage` : "bpm"}</small></div>
      </div>
      <div class="fact ${o2Flag}">
        <div class="k">Oxygen <span class="chip sim">Simulated</span></div>
        <div class="v">${esc(p.spo2 ?? "?")}%<small>${base.spo2 != null ? `was ${esc(base.spo2)}%` : ""}</small></div>
      </div>
      <div class="fact trend">
        <div class="k">Heart rate trend</div>
        ${sparkline(p.hr_history, base.hr)}
      </div>
      ${p.camera_state ? `<div class="fact ${camFlag}">
        <div class="k">Camera sees</div>
        <div class="v text">${esc(CAMERA_LABEL[p.camera_state] || p.camera_state)}</div>
      </div>` : ""}
      ${elevated ? "" : `<div class="actions inline"><button data-action="called_in">Called in</button></div>`}
    </div>
    ${elevated ? `<div class="why">
      <div class="label">Why ${p.corroborated ? `<span class="chip agree">Two signals agree</span>` : ""}</div>
      ${noteText(p) ? `<div>${esc(noteText(p))}</div>` : ""}
      ${lines.length ? `<ul>${lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : ""}
      <div class="do">${esc(TIER_DO[p.tier] || "")}</div>
    </div>` : ""}
    ${elevated ? `<div class="actions">
      ${p.acknowledged ? "" : `<button class="primary" data-action="acknowledge">I'm on it</button>`}
      <button data-action="clear">Reassessed, clear</button>
      <button data-action="called_in">Called in</button>
    </div>` : ""}`;
}

function compactCard(p) {
  const desc = [p.ctas != null ? ctasText(p.ctas) : "", p.complaint, p.waited_min != null ? `waiting ${waited(p.waited_min)}` : ""]
    .filter(Boolean).join(" · ");
  return `
    <div class="who">${esc(displayName(p))}</div>
    <div class="c-desc">${esc(desc)}</div>
    <div class="c-vitals">Heart rate <b>${esc(p.hr ?? "?")}</b> · Oxygen <b>${esc(p.spo2 ?? "?")}%</b> <span class="chip sim">Simulated</span></div>
    <div class="tier">${esc(TIER_LABEL[p.tier] || p.tier)}</div>
    <button data-action="called_in">Called in</button>`;
}

function renderCards(patients) {
  const cards = $("cards");
  // Server order is priority order. Camera-watched and elevated patients get full cards;
  // background patients who are only Watching follow as compact rows, still in server order.
  const isFull = (p) => p.seat || p.tier !== "watching";
  const ordered = [...patients.filter(isFull), ...patients.filter((p) => !isFull(p))];
  const ids = ordered.map((p) => p.id);
  for (const [id, el] of cardEls) {
    if (!ids.includes(id)) { el.remove(); cardEls.delete(id); }
  }
  for (const p of ordered) {
    let el = cardEls.get(p.id);
    if (!el) {
      el = document.createElement("div");
      el.dataset.id = p.id;
      cardEls.set(p.id, el);
    }
    const full = isFull(p);
    setClass(el, `card ${p.tier}${full ? "" : " compact"}`);
    const html = full ? fullCard(p) : compactCard(p);
    if (el._html !== html) { el._html = html; el.innerHTML = html; }
  }
  const current = [...cards.children].map((c) => c.dataset.id);
  if (current.join() !== ids.join()) ids.forEach((id) => cards.appendChild(cardEls.get(id)));
}

/* ---------- Event log ---------- */

function eventText(e) {
  const fixed = EVENT_TEXT[e.kind];
  if (fixed) return fixed;
  return clean(e.detail) || String(e.kind || "").replace(/_/g, " ");
}

function eventRow(e) {
  const who = names[e.patient_id] || (e.patient_id ? `Patient ${e.patient_id}` : "ALLEY");
  return `
    <li class="${esc(e.severity)}">
      <span class="ts">${esc(String(e.ts || "").slice(11, 16))}</span>
      <span><span class="who">${esc(who)}:</span> ${esc(eventText(e))}
        <span class="src">${esc(SOURCE_LABEL[e.source] || e.source)}</span></span>
    </li>`;
}

/* ---------- Soft beep when someone first becomes Check now ---------- */

let audioCtx = null;
let seenNow = null;

function wakeAudio() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    if (!audioCtx) audioCtx = new AC();
    if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
  } catch (err) { /* audio is optional */ }
}

function beep() {
  try {
    wakeAudio();
    if (!audioCtx || audioCtx.state !== "running") return;
    const t0 = audioCtx.currentTime;
    [0, 0.28].forEach((offset, i) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.value = i ? 660 : 880;
      gain.gain.setValueAtTime(0.0001, t0 + offset);
      gain.gain.exponentialRampToValueAtTime(0.18, t0 + offset + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + offset + 0.24);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t0 + offset);
      osc.stop(t0 + offset + 0.26);
    });
  } catch (err) { /* audio is optional */ }
}

function checkBeep(patients) {
  const ids = new Set(patients.filter((p) => p.tier === "check_now").map((p) => p.id));
  if (seenNow) {
    for (const id of ids) if (!seenNow.has(id)) { beep(); break; }
  }
  seenNow = ids;
}

/* ---------- Render ---------- */

const CAMERA_CHIP = { live: "LIVE", starting: "Starting", test: "Test mode", unavailable: "Unavailable", off: "Off" };

function render(state) {
  latest = state;
  const patients = Array.isArray(state.patients) ? state.patients : [];
  const events = Array.isArray(state.events) ? state.events : [];
  $("clock").textContent = String(state.now || "").slice(11, 19);
  $("mode").textContent = state.mode ? `${state.mode.charAt(0).toUpperCase()}${state.mode.slice(1)} mode` : "";
  $("count").textContent = `${patients.length} waiting, most urgent first`;
  for (const p of patients) if (p.name) names[p.id] = p.name;

  renderAlert(patients);
  renderCards(patients);
  checkBeep(patients);

  const evHtml = events.length
    ? events.slice().reverse().map(eventRow).join("")
    : `<li class="empty">Nothing to report yet</li>`;
  const list = $("events");
  if (list._html !== evHtml) { list._html = evHtml; list.innerHTML = evHtml; }

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
  $("cam-chip").textContent = CAMERA_CHIP[state.camera] || state.camera || "Off";
  setClass($("cam-chip"), `chip ${state.camera === "live" ? "live" : "off"}`);
}

/* ---------- Actions ---------- */

async function act(id, action) {
  try {
    const res = await fetch(`/api/patients/${encodeURIComponent(id)}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    const data = await res.json();
    if (data && data.handoff) {
      $("handoff-title").textContent = `Handoff: ${names[id] ? `${names[id]} (${id})` : `Patient ${id}`}`;
      $("handoff-text").textContent = clean(data.handoff);
      if (!$("handoff").open) $("handoff").showModal();
    }
  } catch (err) {
    setConn(false);
  }
}

// pointerdown, not click: the page re-renders every second and a click can straddle a re-render.
// The short lockout stops a double tap from landing on the button that replaces the one just pressed.
let lastAct = 0;
document.addEventListener("pointerdown", (ev) => {
  wakeAudio();
  const btn = ev.target.closest && ev.target.closest("button[data-action]");
  if (!btn || btn.disabled) return;
  const holder = btn.closest("[data-id]");
  if (!holder || !holder.dataset.id) return;
  const t = Date.now();
  if (t - lastAct < 600) return;
  lastAct = t;
  act(holder.dataset.id, btn.dataset.action);
});

function setConn(on) {
  $("conn").textContent = on ? "Connected" : "Reconnecting";
  setClass($("conn"), `chip ${on ? "on" : "warn"}`);
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onmessage = (msg) => {
    let state;
    try { state = JSON.parse(msg.data); } catch (err) { return; }
    render(state);
  };
  ws.onclose = () => { setConn(false); setTimeout(connect, 1000); };
}

// First paint straight from the REST snapshot, then live updates over the WebSocket.
fetch("/api/state").then((r) => r.json()).then((s) => { if (!latest) render(s); }).catch(() => {});
connect();
