"""Keypoints -> raw seat state, plus the per-seat debounce that turns states into events."""
import math

NOSE, L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 0, 5, 6, 11, 12
FACE = (1, 2, 3, 4)  # eyes and ears, used when the nose is not visible
SHOULDER_TILT_SLUMP = 30  # degrees off level: leaning well over to one side


def _mid(kps, conf, a, b, conf_min):
    pts = [kps[i] for i in (a, b) if conf[i] >= conf_min]
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def classify(kps, conf, bbox, conf_min, head_drop_ratio, head_was_visible=False):
    """Return (state, head_ratio). State is "upright", "slumped" or "lying".

    head_ratio = how far the nose sits above the shoulder line, in shoulder-widths
    (about 0.6+ when upright, near 0 or negative when the head has dropped).
    head_was_visible: this person's face was seen while upright, so a face that has now
    vanished between visible shoulders means the head is down, not that they face away.
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

    # Head height: the nose if we can see it, otherwise the eyes and ears.
    head_y = None
    if conf[NOSE] >= conf_min:
        head_y = kps[NOSE][1]
    else:
        face = [kps[i][1] for i in FACE if conf[i] >= conf_min]
        if face:
            head_y = sum(face) / len(face)

    head_ratio = None
    head_dropped = False
    tilt = None
    if both_shoulders:
        dx = abs(kps[L_SHOULDER][0] - kps[R_SHOULDER][0])
        dy = abs(kps[L_SHOULDER][1] - kps[R_SHOULDER][1])
        tilt = math.degrees(math.atan2(dy, dx))
        if head_y is None and head_was_visible:
            head_dropped = True  # face gone, shoulders still there: head is down
    if shoulders and head_y is not None:
        gap = shoulders[1] - head_y  # pixels the head is above the shoulder line
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
        if tilt is None or tilt > 35:
            return "lying", head_ratio
    leaning = tilt is not None and tilt > SHOULDER_TILT_SLUMP
    if head_dropped or leaning or (angle is not None and 30 <= angle <= 60):
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
