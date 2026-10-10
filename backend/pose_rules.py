"""Keypoints -> raw seat state, plus the per-seat debounce that turns states into events."""
import math

NOSE, L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 0, 5, 6, 11, 12


def _mid(kps, conf, a, b, conf_min):
    pts = [kps[i] for i in (a, b) if conf[i] >= conf_min]
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def classify(kps, conf, bbox, conf_min, head_drop_ratio):
    """Return (state, head_ratio). State is "upright", "slumped" or "lying".

    head_ratio = how far the nose sits above the shoulder line, in shoulder-widths
    (about 0.6+ when upright, near 0 or negative when the head has dropped).
    """
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    shoulders = _mid(kps, conf, L_SHOULDER, R_SHOULDER, conf_min)
    hips = _mid(kps, conf, L_HIP, R_HIP, conf_min)
    both_shoulders = conf[L_SHOULDER] >= conf_min and conf[R_SHOULDER] >= conf_min

    # Torso angle from vertical: 0 = upright, 90 = horizontal.
    angle = None
    if shoulders and hips:
        angle = math.degrees(math.atan2(abs(shoulders[0] - hips[0]), hips[1] - shoulders[1]))

    head_ratio = None
    head_dropped = False
    if shoulders and conf[NOSE] >= conf_min:
        gap = shoulders[1] - kps[NOSE][1]  # pixels the nose is above the shoulder line
        if both_shoulders:
            width = math.dist(kps[L_SHOULDER], kps[R_SHOULDER])
            if width > 1:
                head_ratio = gap / width
                head_dropped = head_ratio < head_drop_ratio
        else:
            head_dropped = gap < 0

    if angle is not None and angle > 60:
        return "lying", head_ratio
    if hips is None and w > 1.2 * h:
        # Hips hidden by the chair: wide box means lying, unless it's just a close-up of
        # level shoulders (someone sitting near the camera).
        tilt = 90.0
        if both_shoulders:
            dx = abs(kps[L_SHOULDER][0] - kps[R_SHOULDER][0])
            dy = abs(kps[L_SHOULDER][1] - kps[R_SHOULDER][1])
            tilt = math.degrees(math.atan2(dy, dx))
        if tilt > 35:
            return "lying", head_ratio
    if head_dropped or (angle is not None and 30 <= angle <= 60):
        return "slumped", head_ratio
    return "upright", head_ratio


# (event kind, severity, detail) emitted when a seat settles into a new state
TRANSITION_EVENTS = {
    "slumped": ("head_drop", "moderate", "Head dropping / slumped"),
    "lying": ("lying", "high", "Lying down across seat"),
    "fallen": ("fall", "high", "Possible fall"),
    "empty": ("seat_empty", "info", "Seat empty"),
}


class SeatTracker:
    """Debounce: a new raw state must hold before it counts. Falls count immediately."""

    def __init__(self, hold_seconds, empty_seconds):
        self.hold_seconds = hold_seconds
        self.empty_seconds = empty_seconds
        self.state = "empty"
        self.since = 0.0
        self.candidate = None
        self.candidate_since = 0.0

    def update(self, raw, now):
        """Feed one raw state. Returns a list of (kind, severity, detail) events."""
        if raw == "fallen":
            return self._settle("fallen", now) if self.state != "fallen" else []
        if raw == self.state:
            self.candidate = None
            return []
        if raw != self.candidate:
            self.candidate, self.candidate_since = raw, now
            return []
        hold = self.empty_seconds if raw == "empty" else self.hold_seconds
        if now - self.candidate_since >= hold:
            return self._settle(raw, now)
        return []

    def _settle(self, new, now):
        old = self.state
        self.state, self.since, self.candidate = new, now, None
        if new == "upright":
            if old in ("slumped", "lying", "fallen"):
                return [("recovered", "info", "Upright again")]
            return []
        return [TRANSITION_EVENTS[new]]
