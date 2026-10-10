"""Text for the nurse: the handoff timeline and the one-line alert note.

Claude only words facts that the rules already produced. It never sets or changes a
priority. Every call has a template fallback, so nothing here depends on the network.
"""
import os
from datetime import datetime

from .brain import history_text
from .models import Patient


def fmt_wait(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60}m"


def template_summary(p: Patient, events: list, trend: dict, now: datetime) -> str:
    triaged = datetime.fromisoformat(p.triage_time)
    waited = max(0, int((now - triaged).total_seconds() // 60))
    hx = history_text(p) or "none recorded"

    lines = [
        f"Patient {p.id}{f' ({p.name})' if p.name else ''}: waited {fmt_wait(waited)}. Triaged {triaged:%H:%M}, CTAS {p.ctas}, {p.complaint}. Hx: {hx}.",
        f"Heart rate {trend['hr_start']} → {trend['hr_end']} over the wait (range {trend['hr_min']} to {trend['hr_max']}). "
        f"SpO2 {trend['spo2_start']}% → {trend['spo2_end']}% (range {trend['spo2_min']} to {trend['spo2_max']}%). "
        "Wearable values simulated.",
    ]

    items = []
    for e in events:
        if e.patient_id != p.id:
            continue
        source = "" if e.source == "system" else f" ({e.source})"
        items.append(f"{e.ts[11:16]} {e.detail}{source}.")
    if not items:
        lines.append("No camera or wearable events logged during the wait.")
    else:
        if len(items) > 8:
            lines.append(f"{len(items) - 8} earlier events omitted; see full log.")
            items = items[-8:]
        for i in range(0, len(items), 2):
            lines.append(" ".join(items[i:i + 2]))
    return "\n".join(lines)


RULES = (
    "You write short notes for emergency department nurses from a waiting-room monitoring "
    "system called ALLEY. Use only the facts provided. Do not diagnose, do not suggest a "
    "cause, do not invent or estimate any value, and never say a patient is fine. ALLEY "
    "detects visible signs and vital-sign changes only; use words like 'possible' and "
    "'reassess'. Wearable values are simulated in this demo. Plain clinical language, no "
    "markdown, no em dashes."
)


def ask_claude(system: str, user: str):
    """Return Claude's text, or None on any problem (no key, no network, refusal, timeout)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(timeout=10.0, max_retries=0)
        response = client.messages.create(
            model=os.environ.get("ALLEY_MODEL", "claude-sonnet-5-5"),
            max_tokens=2000,
            system=system,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason != "end_turn":
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or None
    except Exception as exc:
        print(f"[alley] Claude unavailable, using template: {type(exc).__name__}: {exc}")
        return None


def handoff_summary(p: Patient, events: list, trend: dict, now: datetime) -> str:
    template = template_summary(p, events, trend, now)
    first_line = template.splitlines()[0]
    log = "\n".join(
        f"{e.ts[11:16]} [{e.source}] {e.detail}" for e in events if e.patient_id == p.id
    ) or "(no events logged)"
    text = ask_claude(
        RULES + " Write a handoff summary of 4 to 6 short lines, chronological, times as HH:MM. "
        "The first line must be exactly the line given as FIRST LINE.",
        f"FIRST LINE: {first_line}\n\nVITALS TREND (simulated wearable): {trend}\n\nEVENT LOG:\n{log}",
    )
    if text and text.splitlines()[0].strip() == first_line:
        return text
    return template


def alert_note(p: Patient, signals: list, use_llm: bool) -> str:
    who = p.name or f"Patient {p.id}"
    seen = "; ".join(f"{e.detail} ({e.source})" for e in signals) or "signs logged by ALLEY"
    hx = history_text(p)
    template = f"{who}: {seen}." + (f" History: {hx}." if hx else "") + " Reassess now."
    if not use_llm:
        return template
    text = ask_claude(
        RULES + " Write ONE sentence of at most 30 words telling the nurse what ALLEY observed "
        "for this patient, that it may need urgent attention, and that they should be reassessed now.",
        f"Patient: {who}, triage level CTAS {p.ctas}, complaint: {p.complaint}. "
        f"History: {hx or 'none recorded'}. Signals observed: {seen}.",
    )
    return text if text and len(text) < 400 else template
