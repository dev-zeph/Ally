from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Tier(str, Enum):
    WATCHING = "watching"
    CHECK_SOON = "check_soon"
    CHECK_NOW = "check_now"


TIER_RANK = {Tier.WATCHING: 1, Tier.CHECK_SOON: 2, Tier.CHECK_NOW: 3}


@dataclass
class Patient:
    id: str                     # "P7"
    seat: Optional[int]         # 2, or None for background patients off camera
    age: int
    sex: str
    ctas: int                   # 1 most urgent ... 5 least urgent
    complaint: str
    triage_time: str            # ISO
    triage_vitals: dict         # {"hr": 78, "spo2": 98, "sbp": 118}
    history_flags: list = field(default_factory=list)
    history_note: str = ""
    status: str = "waiting"     # "waiting" | "called_in" | "left"


@dataclass
class Event:
    ts: str
    patient_id: str
    source: str                 # "camera" | "wearable" | "companion" | "system"
    kind: str                   # "head_drop", "lying", "fall", "seat_empty", "hr_rise", ...
    severity: str               # "info" | "moderate" | "high"
    detail: str                 # human-readable, e.g. "HR 78 -> 104 (+33%)"
