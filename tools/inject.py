"""Fire a SIMULATED wearable change from a terminal, on this laptop or a second one.

    python3 tools/inject.py spike dacio                 # heart rate spikes within seconds
    python3 tools/inject.py spo2 dacio                  # oxygen drops
    python3 tools/inject.py reset dacio                 # back to triage values
    python3 tools/inject.py spike P7 --host 10.1.6.116  # from another laptop on the same network

Needs only Python 3, no packages. Values are generated, not measured.
"""
import argparse
import json
import urllib.request

SCENARIOS = {"spike": "hr_spike", "climb": "hr_climb", "spo2": "spo2_drop", "stable": "stable", "reset": "reset"}


def main():
    ap = argparse.ArgumentParser(description="Inject a simulated wearable scenario into ALLEY")
    ap.add_argument("what", choices=sorted(SCENARIOS))
    ap.add_argument("patient", help="patient id or name, e.g. P7 or dacio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default=8000, type=int)
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    state = json.load(urllib.request.urlopen(f"{base}/api/state", timeout=5))
    match = [p for p in state["patients"] if args.patient.lower() in (p["id"].lower(), (p.get("name") or "").lower())]
    if not match:
        raise SystemExit(f"No waiting patient called {args.patient!r}. Waiting: "
                         + ", ".join(p.get("name") or p["id"] for p in state["patients"]))
    p = match[0]
    body = json.dumps({"scenario": SCENARIOS[args.what]}).encode()
    req = urllib.request.Request(f"{base}/api/wearable/{p['id']}", body, {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5)
    print(f"SIMULATED wearable: {SCENARIOS[args.what]} sent for {p.get('name') or p['id']} ({p['id']})")


if __name__ == "__main__":
    main()
