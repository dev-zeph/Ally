"""The brain: explainable rules that turn active events into a tier and a ranking.

No model, no LLM. Tiers only go up automatically; only a nurse action lowers them.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .models import TIER_RANK, Patient, Tier

TIER_LABEL = {Tier.WATCHING: "WATCHING", Tier.CHECK_SOON: "CHECK SOON", Tier.CHECK_NOW: "CHECK NOW"}


@dataclass
class PatientState:
    tier: Tier = Tier.WATCHING
    corroborated: bool = False
    acknowledged_at: Optional[str] = None
    cleared_at: Optional[str] = None
    reason: str = ""


def history_text(p: Patient) -> str:
    return p.history_note or ", ".join(f.replace("_", " ") for f in p.history_flags)


class Brain:
    def __init__(self, patients: dict, log):
        self.patients = patients
        self.log = log  # log(patient_id, kind, severity, detail) -> writes a "system" event
        self.states = {pid: PatientState() for pid in patients}

    def evaluate(self, p: Patient, active: list):
        """active = moderate/high events that are ongoing or inside the cross-check window."""
        st = self.states[p.id]
        active = [
            e for e in active
            if e.severity in ("moderate", "high") and (st.cleared_at is None or e.ts > st.cleared_at)
        ]
        sources = {e.source for e in active}
        corroborated = len(sources) >= 2

        if any(e.severity == "high" for e in active):
            candidate = Tier.CHECK_NOW                      # safety floor
        elif corroborated:
            candidate = Tier.CHECK_NOW                      # two independent sources agree
        elif active and (p.ctas <= 3 or p.history_flags):
            candidate = Tier.CHECK_SOON                     # one moderate source, higher-risk patient
        else:
            candidate = Tier.WATCHING                       # one moderate source, low risk: log and watch

        newly_corroborated = corroborated and candidate == Tier.CHECK_NOW and not st.corroborated
        if TIER_RANK[candidate] > TIER_RANK[st.tier]:
            st.tier = candidate
            st.acknowledged_at = None
            tag = " (corroborated)" if newly_corroborated else ""
            self.log(p.id, "escalated", "info", f"Escalated to {TIER_LABEL[candidate]}{tag}")
        elif newly_corroborated:
            self.log(p.id, "corroborated", "info", "Corroborated by " + " + ".join(sorted(sources)))
        if newly_corroborated:
            st.corroborated = True

        if active:
            parts = [f"{e.detail} ({e.source})" for e in sorted(active, key=lambda e: e.ts)]
            hx = history_text(p)
            if hx:
                parts.append(f"Hx: {hx}")
            st.reason = " · ".join(parts)
        elif st.tier == Tier.WATCHING:
            st.reason = ""
        # An elevated tier keeps its last reason until a nurse clears it.

    def acknowledge(self, pid: str):
        st = self.states[pid]
        st.acknowledged_at = datetime.now().isoformat(timespec="seconds")
        self.log(pid, "acknowledged", "info", "Nurse acknowledged")

    def clear(self, pid: str):
        """Nurse reassessed. Back to watching; signals that were already present are not re-raised."""
        now = datetime.now().isoformat(timespec="seconds")
        self.states[pid] = PatientState(cleared_at=now)
        self.log(pid, "cleared", "info", "Nurse reassessed and cleared")

    def sort_key(self, p: Patient, waited_min: int):
        st = self.states[p.id]
        return (-TIER_RANK[st.tier], not st.corroborated, p.ctas, -waited_min)
