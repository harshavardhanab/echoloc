"""Compare detected flyovers with the ADS-B passes a known spot should have heard.

Runs in dopplertrack's environment (it reads dopplertrack's detect output and fetch-day traces):
    cd ../third_party/dopplertrack
    uv run --frozen python -I ../../echoloc/check_passes.py ../../echoloc/data/swfl/swfl_1102 \
        ../../echoloc/data/swfl/traces_2026-11-02.npz --at 26.702359 -81.847766 --rmax 12000

<prefix>.meta.json comes from rewind_capture.py, <prefix>_detected.json (or detected.json in the
same folder) from `dopplertrack detect --out`. Audio seconds are wall seconds after t0 (the WAV
from rewind_capture.py is built on that timeline). Tracks are split wherever ADS-B is silent for
more than --max-gap, and every separate closest approach inside a covered stretch counts, so an
aircraft that comes back is listed again and nothing is inferred across a coverage gap.
Prints each approach heard within --rmax during the recording, which detections line up with
one, and a clock null: the same match count with the detections slid by every offset in +-30 min
(excluding +-60 s).
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
ap.add_argument("--max-gap", type=float, default=60.0, help="split tracks where ADS-B is silent longer (s)")
ap.add_argument("--edge", type=float, default=20.0, help="ignore minima this close to a coverage gap (s)")
a = ap.parse_args()

C = 343.0
pre = Path(a.prefix)
meta = json.load(open(f"{pre}.meta.json"))
det_path = Path(f"{pre}_detected.json")
det = json.load(open(det_path if det_path.exists() else pre.parent / "detected.json"))
t0 = meta["t0"]
dur = meta.get("duration_s") or meta["segments"][-1][1] - t0 + 5.0

def approaches(t, R, edge_s):
    """Indices of separate closest approaches in one continuously covered stretch: local minima
    of range at least edge_s from either end (a minimum at an edge may lie in the gap beyond)."""
    i = np.flatnonzero((R[1:-1] <= R[:-2]) & (R[1:-1] < R[2:])) + 1
    i = i[(t[i] - t[0] >= edge_s) & (t[-1] - t[i] >= edge_s)]
    keep = []
    for j in i[np.argsort(R[i])]:                    # strongest first; one per 120 s
        if all(abs(t[j] - t[k]) > 120 for k in keep):
            keep.append(j)
    return sorted(keep)


rows, dropped = [], 0
for tr in load_traces(a.traces):
    k = (tr.t > t0 - 900) & (tr.t < t0 + dur + 900) & ~tr.on_ground
    if k.sum() < 5:
        continue
    t = tr.t[k]
    enu = lla_to_enu(tr.lat[k], tr.lon[k], tr.alt[k], *a.at, a.alt)
    brk = np.flatnonzero(np.diff(t) > a.max_gap) + 1           # ADS-B coverage gaps
    for s0, s1 in zip(np.r_[0, brk], np.r_[brk, len(t)]):
        if s1 - s0 < 3:
            continue
        tt = np.arange(t[s0], t[s1 - 1] + 0.5, 1.0)
        E, N, U = (np.interp(tt, t[s0:s1], enu[s0:s1, c]) for c in range(3))
        R = np.sqrt(E**2 + N**2 + U**2)
        idx = approaches(tt, R, a.edge)
        jm = int(np.argmin(R))
        if R[jm] <= a.rmax and jm not in idx and t0 <= tt[jm] + R[jm] / C <= t0 + dur:
            dropped += 1                                         # closest point is at a gap edge
        for j in idx:
            th = tt[j] + R[j] / C
            if R[j] <= a.rmax and t0 <= th <= t0 + dur:
                rows.append((th - t0, R[j], U[j], tr.reg or tr.icao, tr.typ or "?"))
rows.sort()

E_t = np.array([e["t_cpa"] for e in det])
print(f"{len(rows)} measured non-jet approaches within {a.rmax / 1000:.0f} km heard during the recording; "
      f"{len(E_t)} detections; {dropped} track pieces whose closest point falls in an ADS-B gap were left out")
for th, R, u, reg, typ in rows:
    near = [f"det {e['t_cpa']:.1f} (dt {e['t_cpa'] - th:+.1f} s, score {e['score']:.2f})" for e in det if abs(e["t_cpa"] - th) < 30]
    print(f"  {th:8.1f} s  slant {R:6.0f} m  up {u:5.0f} m  {reg:8s} {typ:5s} {' '.join(near)}")

P = np.array([r[0] for r in rows])
def matches(shift, tol):
    return int(sum(len(P) and np.min(np.abs(P - (e + shift))) <= tol for e in E_t))
shifts = np.arange(-1800, 1801, 5.0)
shifts = shifts[np.abs(shifts) > 60]
for tol in (1, 5, 15, 30):
    real = matches(0, tol)
    null = np.array([matches(s, tol) for s in shifts])
    print(f"tol +-{tol:2d} s: {real} detections match a pass; slid clocks mean {null.mean():.2f}, p(null >= real) {np.mean(null >= real):.3f}")
