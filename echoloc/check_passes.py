"""Compare detected flyovers with the ADS-B passes a known spot should have heard.

Runs in dopplertrack's environment (it reads dopplertrack's detect output and fetch-day traces):
    cd ../third_party/dopplertrack
    uv run --frozen python -I ../../echoloc/check_passes.py ../../echoloc/data/swfl/swfl_1102 \
        ../../echoloc/data/swfl/traces_2026-11-02.npz --at 26.702359 -81.847766 --rmax 12000

<prefix>.meta.json comes from rewind_capture.py, <prefix>_detected.json (or detected.json in the
same folder) from `dopplertrack detect --out`. Prints every pass heard within --rmax during the
recording, which detections line up with one, and a clock null: the same match count with the
detections slid by every offset in +-30 min (excluding +-60 s).
"""
import argparse
import json
from pathlib import Path

import numpy as np
from dopplertrack.adsb import load_traces
from dopplertrack.geo import lla_to_enu

ap = argparse.ArgumentParser()
ap.add_argument("prefix")
ap.add_argument("traces")
ap.add_argument("--at", nargs=2, type=float, required=True, metavar=("LAT", "LON"))
ap.add_argument("--alt", type=float, default=20.0)
ap.add_argument("--rmax", type=float, default=12000.0)
a = ap.parse_args()

C = 343.0
pre = Path(a.prefix)
meta = json.load(open(f"{pre}.meta.json"))
det_path = Path(f"{pre}_detected.json")
det = json.load(open(det_path if det_path.exists() else pre.parent / "detected.json"))
t0 = meta["t0"]
dur = meta["segments"][-1][1] - t0 + 5.0

rows = []
for tr in load_traces(a.traces):
    k = (tr.t > t0 - 300) & (tr.t < t0 + dur + 300) & ~tr.on_ground
    if k.sum() < 5:
        continue
    t = tr.t[k]
    tt = np.arange(t[0], t[-1], 1.0)
    e, n, u = lla_to_enu(tr.lat[k], tr.lon[k], tr.alt[k], *a.at, a.alt).T
    E, N, U = (np.interp(tt, t, x) for x in (e, n, u))
    R = np.sqrt(E**2 + N**2 + U**2)
    i = np.argmin(R)
    th = tt[i] + R[i] / C
    gap = np.diff(t)[min(np.searchsorted(t, tt[i]), len(t) - 1) - 1] if len(t) > 1 else 0
    if R[i] <= a.rmax and t0 <= th <= t0 + dur:
        rows.append((th - t0, R[i], U[i], tr.reg or tr.icao, tr.typ or "?", gap))
rows.sort()

E_t = np.array([e["t_cpa"] for e in det])
print(f"{len(rows)} non-jet passes within {a.rmax / 1000:.0f} km heard during the recording; {len(E_t)} detections")
for th, R, u, reg, typ, gap in rows:
    near = [f"det {e['t_cpa']:.1f} (dt {e['t_cpa'] - th:+.1f} s, score {e['score']:.2f})" for e in det if abs(e["t_cpa"] - th) < 30]
    print(f"  {th:8.1f} s  slant {R:6.0f} m  up {u:5.0f} m  {reg:8s} {typ:5s} adsb gap {gap:4.0f} s  {' '.join(near)}")

P = np.array([r[0] for r in rows])
def matches(shift, tol):
    return int(sum(len(P) and np.min(np.abs(P - (e + shift))) <= tol for e in E_t))
shifts = np.arange(-1800, 1801, 5.0)
shifts = shifts[np.abs(shifts) > 60]
for tol in (1, 5, 15, 30):
    real = matches(0, tol)
    null = np.array([matches(s, tol) for s in shifts])
    print(f"tol +-{tol:2d} s: {real} detections match a pass; slid clocks mean {null.mean():.2f}, p(null >= real) {np.mean(null >= real):.3f}")
