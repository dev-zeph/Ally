"""SIMULATED wearable: one HR/SpO2 stream per patient, driven from control.html.

Nothing here reads a real sensor. Every value is generated.
"""
import random
from collections import deque
from datetime import datetime

from .models import Event, Patient

SCENARIOS = ("stable", "hr_climb", "spo2_drop", "reset")


class Stream:
    def __init__(self, p: Patient, now: datetime):
        self.pid = p.id
        self.base_hr = p.triage_vitals["hr"]
        self.base_spo2 = p.triage_vitals["spo2"]
        self.level_hr = float(self.base_hr)      # noiseless value the scenario is steering
        self.level_spo2 = float(self.base_spo2)
        self.hr = self.level_hr
        self.spo2 = self.level_spo2
        self.scenario = "stable"
        self.t0 = now
        self.from_hr = self.level_hr
        self.from_spo2 = self.level_spo2
        self.history = deque(maxlen=90)          # HR per tick, for the sparkline
        self.recent = deque(maxlen=5)            # short rolling window used for threshold checks
        self.min_hr = self.max_hr = self.base_hr
        self.min_spo2 = self.max_spo2 = self.base_spo2
        self.since = {}                          # kind -> when the condition first became true
        self.active = {}                         # kind -> when it was confirmed (sustained)

    def avg(self):
        if not self.recent:
            return self.hr, self.spo2
        n = len(self.recent)
        return sum(r[0] for r in self.recent) / n, sum(r[1] for r in self.recent) / n

    def trend(self) -> dict:
        hr, spo2 = self.avg()
        return {
            "hr_start": self.base_hr, "hr_end": round(hr), "hr_min": round(self.min_hr), "hr_max": round(self.max_hr),
            "spo2_start": self.base_spo2, "spo2_end": round(spo2),
            "spo2_min": round(self.min_spo2), "spo2_max": round(self.max_spo2),
        }


class WearableSim:
    def __init__(self, patients: dict, settings: dict, timers: dict):
        now = datetime.now()
        self.patients = patients
        self.vitals = settings["vitals"]        # CLINICIAN-CONFIGURABLE thresholds
        self.sim = settings["sim"]
        self.timers = timers
        self.streams = {pid: Stream(p, now) for pid, p in patients.items()}

    def set_scenario(self, pid: str, name: str):
        s = self.streams[pid]
        if name == "reset":
            s.level_hr, s.level_spo2 = float(s.base_hr), float(s.base_spo2)
            name = "stable"
        s.scenario = name
        s.t0 = datetime.now()
        s.from_hr, s.from_spo2 = s.level_hr, s.level_spo2

    def set_manual(self, pid: str, hr=None, spo2=None):
        s = self.streams[pid]
        s.scenario = "manual"
        if hr is not None:
            s.level_hr = float(hr)
        if spo2 is not None:
            s.level_spo2 = float(spo2)

    def tick(self, now: datetime) -> list:
        """Advance every stream one second. Returns newly confirmed wearable events."""
        events = []
        for pid, s in self.streams.items():
            if self.patients[pid].status != "waiting":
                continue
            frac = min(1.0, (now - s.t0).total_seconds() / self.timers["scenario_ramp_seconds"])
            if s.scenario == "hr_climb":
                target = s.base_hr + self.sim["hr_climb_bpm"]
                s.level_hr = s.from_hr + (target - s.from_hr) * frac
            elif s.scenario == "spo2_drop":
                s.level_spo2 = s.from_spo2 + (self.sim["spo2_drop_target"] - s.from_spo2) * frac

            s.hr = s.level_hr + random.uniform(-2, 2)
            s.spo2 = min(100.0, s.level_spo2 + random.uniform(-0.5, 0.5))
            s.recent.append((s.hr, s.spo2))
            s.history.append(round(s.hr))
            s.min_hr, s.max_hr = min(s.min_hr, s.hr), max(s.max_hr, s.hr)
            s.min_spo2, s.max_spo2 = min(s.min_spo2, s.spo2), max(s.max_spo2, s.spo2)

            for kind, (on, severity, detail) in self._checks(s).items():
                if not on:
                    s.since.pop(kind, None)
                    s.active.pop(kind, None)
                    continue
                s.since.setdefault(kind, now)
                held = (now - s.since[kind]).total_seconds()
                if kind not in s.active and held >= self.timers["vitals_sustain_seconds"]:
                    s.active[kind] = now
                    events.append(Event(now.isoformat(timespec="seconds"), pid, "wearable", kind, severity, detail))
        return events

    def _checks(self, s: Stream) -> dict:
        v = self.vitals
        hr, spo2 = s.avg()
        pct = round((hr / s.base_hr - 1) * 100)
        return {
            "hr_rise": (hr >= s.base_hr * v["hr_rise_ratio"], "moderate", f"HR {s.base_hr} → {round(hr)} (+{pct}%)"),
            "hr_high": (hr >= v["hr_high"], "high", f"HR {round(hr)}, high"),
            "spo2_low": (spo2 <= v["spo2_low"], "high", f"SpO2 {round(spo2)}%, low"),
            "spo2_drop": (spo2 <= s.base_spo2 - v["spo2_drop"], "moderate", f"SpO2 {s.base_spo2}% → {round(spo2)}%"),
        }

    def conditions(self, pid: str) -> list:
        """Wearable conditions that are true right now, with live detail text."""
        s = self.streams[pid]
        checks = self._checks(s)
        kinds = set(s.active)
        # The high event already says it; don't repeat the moderate one beside it.
        if "hr_high" in kinds:
            kinds.discard("hr_rise")
        if "spo2_low" in kinds:
            kinds.discard("spo2_drop")
        return [
            Event(s.active[k].isoformat(timespec="seconds"), pid, "wearable", k, checks[k][1], checks[k][2])
            for k in sorted(kinds)
        ]
