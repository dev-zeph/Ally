"""Handoff timeline written when a patient is called in.

Template version: built only from logged facts, works with no network.
"""
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
