"""ALLEY backend: FastAPI app, WebSocket state push, main 1 s loop.

Run from the repo root:
    .venv/bin/uvicorn backend.main:app --port 8000 --timeout-graceful-shutdown 1
"""
import asyncio
import json
import os
import traceback
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .brain import Brain
from .camera import Camera
from .handoff import template_summary
from .models import Event, Patient
from .wearable_sim import SCENARIOS, WearableSim

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
FRONTEND = ROOT / "frontend"

SETTINGS = json.loads((CONFIG / "settings.json").read_text())
TIMERS = SETTINGS[SETTINGS["mode"]]
SEATS = json.loads((CONFIG / "seats.json").read_text())

PATIENTS = {}
EVENTS = []      # full log, newest last
HANDOFFS = {}    # patient id -> handoff text
brain = None
sim = None
camera = Camera(SETTINGS, TIMERS, SEATS)

CAMERA_CONDITIONS = {  # held seat state -> (kind, severity, label)
    "slumped": ("head_drop", "moderate", "Head dropping"),
    "lying": ("lying", "high", "Lying down"),
    "fallen": ("fall", "high", "Possible fall"),
}


def stamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def log_system(pid, kind, severity, detail):
    EVENTS.append(Event(stamp(datetime.now()), pid, "system", kind, severity, detail))


def init_state():
    """Load the mock records and start everyone at Watching. Also used by the demo reset."""
    global brain, sim
    PATIENTS.clear()
    PATIENTS.update({p["id"]: Patient(**p) for p in json.loads((CONFIG / "patients.json").read_text())})
    EVENTS.clear()
    HANDOFFS.clear()
    brain = Brain(PATIENTS, log_system)
    sim = WearableSim(PATIENTS, SETTINGS, TIMERS)
    camera.reset()


def waiting():
    return [p for p in PATIENTS.values() if p.status == "waiting"]


def patient_in_seat(seat):
    return next((p for p in waiting() if p.seat == seat), None)


def minutes_waited(p: Patient, now: datetime) -> int:
    return max(0, int((now - datetime.fromisoformat(p.triage_time)).total_seconds() // 60))


def camera_conditions(p: Patient, now: datetime) -> list:
    """What the camera sees for this patient's seat right now, as a live event."""
    held = camera.seat_state(p.seat) if p.seat else None
    if not held or held[0] not in CAMERA_CONDITIONS:
        return []
    state, seconds = held
    kind, severity, label = CAMERA_CONDITIONS[state]
    onset = stamp(now - timedelta(seconds=seconds))
    return [Event(onset, p.id, "camera", kind, severity, f"{label} {int(seconds)}s")]


def tick():
    now = datetime.now()
    EVENTS.extend(sim.tick(now))
    if camera.status == "test":
        camera.step_forced()
    for seat, kind, severity, detail in camera.drain_events():
        p = patient_in_seat(seat)
        if p:
            EVENTS.append(Event(stamp(now), p.id, "camera", kind, severity, detail))

    window_start = stamp(now - timedelta(seconds=TIMERS["corroboration_window_seconds"]))
    for p in waiting():
        live = sim.conditions(p.id) + camera_conditions(p, now)
        # A source that is reporting something right now speaks for itself; logged events
        # only stand in for sources that have gone quiet inside the cross-check window.
        live_sources = {e.source for e in live}
        active = {(e.source, e.kind): e for e in EVENTS
                  if e.patient_id == p.id and e.ts >= window_start
                  and e.source != "system" and e.source not in live_sources}
        active.update({(e.source, e.kind): e for e in live})
        brain.evaluate(p, list(active.values()))


def build_state() -> dict:
    """Full state snapshot pushed to the dashboard and control panel."""
    now = datetime.now()
    patients = []
    for p in waiting():
        st = brain.states[p.id]
        s = sim.streams[p.id]
        held = camera.seat_state(p.seat) if p.seat and camera.status in ("live", "test") else None
        d = asdict(p)
        d.update(
            waited_min=minutes_waited(p, now),
            tier=st.tier.value,
            corroborated=st.corroborated,
            acknowledged=bool(st.acknowledged_at),
            reason=st.reason,
            hr=round(s.hr),
            spo2=round(s.spo2),
            hr_history=list(s.history),
            scenario=s.scenario,
            camera_state=held[0] if held else None,
        )
        patients.append(d)
    patients.sort(key=lambda d: brain.sort_key(PATIENTS[d["id"]], d["waited_min"]))
    return {
        "now": stamp(now),
        "mode": SETTINGS["mode"],
        "camera": camera.status,
        "patients": patients,
        "events": [asdict(e) for e in EVENTS[-60:]],
    }


async def tick_loop():
    while True:
        try:
            tick()
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_state()
    camera.start()
    task = asyncio.create_task(tick_loop())
    yield
    task.cancel()
    camera.stop()


app = FastAPI(title="ALLEY", lifespan=lifespan)


def get_patient(pid: str) -> Patient:
    p = PATIENTS.get(pid)
    if not p or p.status != "waiting":
        raise HTTPException(404, f"No waiting patient {pid}")
    return p


@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    try:
        while True:
            await socket.send_json(build_state())
            await asyncio.sleep(1)
    except (WebSocketDisconnect, RuntimeError):
        pass


@app.get("/api/state")
def api_state():
    return build_state()


@app.post("/api/patients/{pid}/action")
def nurse_action(pid: str, action: str = Body(embed=True)):
    p = get_patient(pid)
    if action == "acknowledge":
        brain.acknowledge(pid)
    elif action == "clear":
        brain.clear(pid)
    elif action == "called_in":
        now = datetime.now()
        HANDOFFS[pid] = template_summary(p, EVENTS, sim.streams[pid].trend(), now)
        p.status = "called_in"
        log_system(pid, "called_in", "info", "Called in; handoff written")
        return {"ok": True, "handoff": HANDOFFS[pid]}
    else:
        raise HTTPException(400, f"Unknown action {action}")
    return {"ok": True}


@app.post("/api/wearable/{pid}")
def wearable_control(pid: str, body: dict = Body()):
    """SIMULATED WEARABLE control: pick a scenario or set values by hand."""
    get_patient(pid)
    if "scenario" in body:
        if body["scenario"] not in SCENARIOS:
            raise HTTPException(400, f"Unknown scenario {body['scenario']}")
        sim.set_scenario(pid, body["scenario"])
    else:
        sim.set_manual(pid, body.get("hr"), body.get("spo2"))
    return {"ok": True}


@app.post("/api/reset")
def reset_demo():
    init_state()
    return {"ok": True}


if os.environ.get("ALLEY_NO_CAMERA"):
    @app.post("/api/test/seat/{seat}")
    def test_seat(seat: int, state: str = Body(embed=True)):
        """Camera-less testing only: set a seat's raw state by hand."""
        camera.forced[seat] = state
        return {"ok": True}


@app.get("/video")
async def video():
    """Live annotated preview (MJPEG). Streamed from memory, never stored."""
    if camera.status not in ("starting", "live"):
        raise HTTPException(503, "Camera not available")

    async def frames():
        while True:
            if camera.jpeg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + camera.jpeg + b"\r\n"
            await asyncio.sleep(0.08)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
