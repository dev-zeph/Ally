"""Live camera: capture + pose inference in a background thread.

Frames are processed in memory and discarded. Nothing is written to disk.
No face detection or recognition: people are matched to a seat zone only.
"""
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path

from .pose_rules import SeatTracker, classify

ROOT = Path(__file__).resolve().parent.parent

STATE_COLOR = {  # BGR
    "upright": (94, 197, 34), "slumped": (36, 165, 245), "lying": (68, 68, 239),
    "fallen": (68, 68, 239), "empty": (138, 122, 107),
}
SKELETON = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (0, 5), (0, 6)]
FALL_WINDOW_SECONDS = 1.5
FALL_DROP_FRACTION = 0.25
LOST_GRACE_SECONDS = 1.0  # ignore brief detection dropouts
BASELINE_SAMPLES = 15     # upright frames needed before the personal head baseline is trusted


class Camera:
    def __init__(self, settings: dict, timers: dict, seats_cfg: dict):
        self.pose = settings["pose"]
        self.timers = timers
        self.seats_cfg = seats_cfg
        self.status = "off"  # off | starting | live | unavailable | test
        self.jpeg = None
        self.events = queue.Queue()  # (seat, kind, severity, detail)
        self.lock = threading.Lock()
        self.running = False
        self.cap = None
        self.forced = {}  # seat -> raw state, test mode only
        self.labels = {}  # seat -> name drawn on the preview
        self.reset()

    def reset(self):
        with self.lock:
            self.trackers = {
                s["seat"]: SeatTracker(self.timers["state_hold_seconds"], self.timers["seat_empty_seconds"])
                for s in self.seats_cfg["seats"]
            }
            self.centers = {s["seat"]: deque() for s in self.seats_cfg["seats"]}
            self.last_seen = {s["seat"]: (0.0, "empty") for s in self.seats_cfg["seats"]}
            self.forced = {}
            self.last_x = {}  # seat -> last horizontal position (0..1), handheld mode
            self.baseline = {}  # seat -> (usual upright head ratio, samples seen)

    def start(self):
        """Call from the main thread: macOS only shows the camera permission prompt there."""
        if os.environ.get("ALLEY_NO_CAMERA"):
            self.status = "test"  # no camera; seat states can be set by hand for testing
            return
        import cv2
        # "source" can be a stream URL from a phone camera app; otherwise use the local camera index.
        self.cap = cv2.VideoCapture(self.pose.get("source") or self.pose["camera_index"])
        if not self.cap.isOpened():
            self.status = "unavailable"
            return
        self.status = "starting"
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.running = False

    def seat_state(self, seat):
        """(held state, seconds held) for a seat, or None if the seat has no zone."""
        with self.lock:
            t = self.trackers.get(seat)
            return (t.state, time.time() - t.since) if t else None

    def drain_events(self):
        out = []
        while not self.events.empty():
            out.append(self.events.get_nowait())
        return out

    def step_forced(self):
        """Test mode only: push hand-set raw states through the same debounce."""
        now = time.time()
        with self.lock:
            for seat, t in self.trackers.items():
                for ev in t.update(self.forced.get(seat, "upright"), now):
                    self.events.put((seat, *ev))

    def _run(self):
        import cv2
        from ultralytics import YOLO
        model = YOLO(str(ROOT / self.pose["model"]))
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.1)
                continue
            result = model(frame, verbose=False)[0]
            self._process(cv2, frame, result, time.time())
            self.status = "live"
        self.cap.release()

    def _assign(self, people, w, h):
        """Match people to seats. Returns seat -> (person or None, box to draw or None)."""
        seats = [s["seat"] for s in self.seats_cfg["seats"]]
        if self.pose.get("seat_mode") == "order":
            # Handheld camera: no fixed zones. Take the largest people in view (ignoring
            # anyone small in the background) and number them left to right.
            def cx(p):
                return (p[0][0] + p[0][2]) / 2 / w
            min_h = self.pose.get("min_person_height", 0.25) * h
            near = [p for p in people if p[0][3] - p[0][1] >= min_h]
            near = sorted(near, key=lambda p: (p[0][2] - p[0][0]) * (p[0][3] - p[0][1]), reverse=True)[:len(seats)]
            near.sort(key=cx)
            if len(near) == len(seats) or not self.last_x:
                pairs = dict(zip(seats, near))
            else:
                # Someone dropped out of view: keep the others on the seat they were nearest to.
                pairs, free = {}, list(seats)
                for p in near:
                    seat = min(free, key=lambda s: abs(self.last_x.get(s, 9) - cx(p)))
                    pairs[seat] = p
                    free.remove(seat)
            for seat, p in pairs.items():
                self.last_x[seat] = cx(p)
            return {s: (pairs.get(s), tuple(int(v) for v in pairs[s][0]) if s in pairs else None) for s in seats}

        sx = w / self.seats_cfg["frame"]["width"]
        sy = h / self.seats_cfg["frame"]["height"]
        out = {}
        for s in self.seats_cfg["seats"]:
            x1, y1, x2, y2 = int(s["x1"] * sx), int(s["y1"] * sy), int(s["x2"] * sx), int(s["y2"] * sy)
            inside = [
                p for p in people
                if x1 <= (p[0][0] + p[0][2]) / 2 <= x2 and y1 <= (p[0][1] + p[0][3]) / 2 <= y2
            ]
            person = max(inside, key=lambda p: (p[0][2] - p[0][0]) * (p[0][3] - p[0][1])) if inside else None
            out[s["seat"]] = (person, (x1, y1, x2, y2))
        return out

    def _process(self, cv2, frame, result, now):
        h, w = frame.shape[:2]
        people = []
        if result.keypoints is not None and result.keypoints.conf is not None and len(result.boxes):
            boxes = result.boxes.xyxy.cpu().numpy()
            kxy = result.keypoints.xy.cpu().numpy()
            kconf = result.keypoints.conf.cpu().numpy()
            people = list(zip(boxes, kxy, kconf))

        for box, kps, conf in people:
            for a, b in SKELETON:
                if conf[a] >= self.pose["kp_conf_min"] and conf[b] >= self.pose["kp_conf_min"]:
                    cv2.line(frame, tuple(map(int, kps[a])), tuple(map(int, kps[b])), (255, 255, 255), 2)

        with self.lock:
            for seat, (person, rect) in self._assign(people, w, h).items():
                head_ratio = None
                if person is not None:
                    box, kps, conf = person
                    # Head drop is judged against this person's own upright posture once we've
                    # seen enough of it; before that, only a very clear drop counts.
                    base, n = self.baseline.get(seat, (0.0, 0))
                    threshold = self.pose["head_drop_ratio"]
                    if n >= BASELINE_SAMPLES:
                        threshold = max(threshold, base * self.pose["head_drop_fraction"])
                    raw, head_ratio = classify(kps, conf, box, self.pose["kp_conf_min"], threshold,
                                                head_was_visible=n >= BASELINE_SAMPLES)
                    if raw == "upright" and head_ratio is not None:
                        weight = 1 / (n + 1) if n < BASELINE_SAMPLES else 0.02
                        self.baseline[seat] = (base + (head_ratio - base) * weight, n + 1)
                    if self.pose.get("fall_detection", True):
                        cy = (box[1] + box[3]) / 2
                        centers = self.centers[seat]
                        centers.append((now, cy))
                        while centers and now - centers[0][0] > FALL_WINDOW_SECONDS:
                            centers.popleft()
                        if cy - min(c[1] for c in centers) > FALL_DROP_FRACTION * h:
                            raw = "fallen"
                    self.last_seen[seat] = (now, raw)
                else:
                    seen_at, last_raw = self.last_seen[seat]
                    raw = last_raw if now - seen_at < LOST_GRACE_SECONDS else "empty"
                    if raw == "empty":
                        self.centers[seat].clear()

                tracker = self.trackers[seat]
                for ev in tracker.update(raw, now):
                    self.events.put((seat, *ev))

                if rect is None:
                    continue
                x1, y1, x2, y2 = rect
                color = STATE_COLOR[tracker.state]
                label = f"{self.labels.get(seat, f'Seat {seat}')}: {tracker.state}"
                if head_ratio is not None:
                    label += f"  head {head_ratio:.2f} / {threshold:.2f}"
                scale = max(0.7, w / 1280 * 0.8)
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
                cv2.rectangle(frame, (x1, y1), (x1 + tw + 16, y1 + th + 16), color, -1)
                cv2.putText(frame, label, (x1 + 8, y1 + th + 8), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 2)

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            self.jpeg = buf.tobytes()  # held in memory for the live preview only
