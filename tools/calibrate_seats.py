"""Draw seat zones on the live camera view and save them to config/seats.json.

Run from the repo root:  .venv/bin/python tools/calibrate_seats.py
Click two opposite corners for seat 1, then two for seat 2, and so on.
Keys: s = save and quit, r = start over, q = quit without saving.
Frames are only shown on screen, never saved.
"""
import json
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((ROOT / "config" / "settings.json").read_text())
OUT = ROOT / "config" / "seats.json"
WINDOW = "ALLEY seat calibration: click 2 corners per seat (s save, r reset, q quit)"

points = []


def on_click(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        points.append((x, y))


def main():
    cap = cv2.VideoCapture(SETTINGS["pose"]["camera_index"])
    if not cap.isOpened():
        raise SystemExit("Could not open the camera. Check camera permission for your terminal app.")
    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, on_click)
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        for i in range(0, len(points) - 1, 2):
            cv2.rectangle(frame, points[i], points[i + 1], (94, 197, 34), 3)
            cv2.putText(frame, f"Seat {i // 2 + 1}", (min(points[i][0], points[i + 1][0]) + 8,
                        min(points[i][1], points[i + 1][1]) + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (94, 197, 34), 2)
        if len(points) % 2:
            cv2.circle(frame, points[-1], 6, (36, 165, 245), -1)
        cv2.imshow(WINDOW, frame)
        key = cv2.waitKey(30) & 0xFF
        if key == ord("r"):
            points.clear()
        elif key == ord("q"):
            break
        elif key == ord("s"):
            seats = []
            for i in range(0, len(points) - 1, 2):
                (xa, ya), (xb, yb) = points[i], points[i + 1]
                seats.append({"seat": i // 2 + 1, "x1": min(xa, xb), "y1": min(ya, yb),
                              "x2": max(xa, xb), "y2": max(ya, yb)})
            OUT.write_text(json.dumps({"frame": {"width": w, "height": h}, "seats": seats}, indent=2) + "\n")
            print(f"Saved {len(seats)} seats to {OUT}")
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
