"""Locate a real recording.

python run_clip.py --audio clip.m4a --start 2026-11-02T14:03:10Z --region 51.48,-0.35 \
    --search-km 10 [--truth 51.479,-0.333] [--clock-err 30] [--adsblol-log data/live.jsonl]

--start      UTC time of the first audio sample (best guess)
--clock-err  how far off --start might be (s); searched in 2 s steps
--adsblol-log use a live poll log instead of adsb.aero history
"""
import argparse
import json
from datetime import datetime

import numpy as np

import adsb
from locate import Locator
from physics import Frame
from spec import LogSpec, load_audio

ap = argparse.ArgumentParser()
ap.add_argument("--audio", required=True)
ap.add_argument("--start", required=True)
ap.add_argument("--region", required=True)
ap.add_argument("--search-km", type=float, default=10)
ap.add_argument("--truth")
ap.add_argument("--clock-err", type=float, default=6)
ap.add_argument("--adsblol-log")
ap.add_argument("--geoid", type=float, default=None, help="geoid height (m); estimated if omitted")
ap.add_argument("--out", default="data/clip_result.json")
a = ap.parse_args()

t0 = datetime.fromisoformat(a.start.replace("Z", "+00:00")).timestamp()
clat, clon = map(float, a.region.split(","))
frame = Frame(clat, clon)
x = load_audio(a.audio)
dur = len(x) / 8000
print(f"audio {dur:.0f}s")
elev = adsb.ground_elevation(clat, clon)
if a.adsblol_log:
    tracks = adsb.load_adsblol_log(a.adsblol_log, ground_alt_m=elev, geoid_m=a.geoid)
else:
    tracks = adsb.fetch_aero(clat, clon, (a.search_km + 8) * 1000, t0 - a.clock_err - 300,
                             t0 + a.clock_err + dur + 300,
                             cache=f"data/aero_{clat:.3f}_{clon:.3f}_{int(t0)}_{int(dur)}.json")
print(f"{len(tracks)} tracks")
spec = LogSpec(x)
loc = Locator(spec, tracks, frame, t0)
cal = loc.calibrate(a.search_km * 1000, elev + 1.5)
print("noise z (wrong clocks): n=%d p50 %.2f p90 %.2f p99 %.2f" % (len(cal), *np.percentile(cal, [50, 90, 99])))
offsets = list(np.arange(-a.clock_err, a.clock_err + 0.1, 2.0))
res = loc.search(0, 0, a.search_km * 1000, elev + 1.5, offsets)
null = loc.search(0, 0, a.search_km * 1000, elev + 1.5, [o + 397 for o in offsets], top=1)
null_best = null["candidates"][0][0] if null["candidates"] else 0.0
truth = None
if a.truth:
    tl = list(map(float, a.truth.split(",")))
    tx, ty = frame.to_xy(*tl)
    truth = np.array([float(tx), float(ty), adsb.ground_elevation(*tl) + 1.5])
    ts_, info = loc.score(truth[None, :], offsets, detail=True)
    o = int(ts_[0].argmax())
    print(f"score at truth {ts_[0, o]:.1f} at offset {offsets[o]:+.0f}s; events:")
    for e in info[0][o]:
        print("   ", e)
print(f"null (clock wrong by 397 s): {null_best:.1f}")
out = {"null_best": null_best, "candidates": []}
for s, q, off in res["candidates"]:
    plat, plon = frame.to_latlon(q[0], q[1])
    _, info = loc.score(np.array([[q[0], q[1], elev + 1.5]]), [off], detail=True)
    c = {"score": s, "lat": float(plat), "lon": float(plon), "offset": off, "events": info[0][0]}
    if truth is not None:
        c["err_m"] = float(np.hypot(q[0] - truth[0], q[1] - truth[1]))
    out["candidates"].append(c)
    print(f"  {s:6.1f}  {float(plat):.5f},{float(plon):.5f}  off {off:+.0f}s"
          + (f"  err {c['err_m']:.0f} m" if truth is not None else "")
          + "  " + ", ".join(f"{e['callsign']}:{e['z']}" for e in info[0][0] if e["z"] > 5))
obs, shape, S = res["coarse"]
np.savez(a.out.replace(".json", ".npz"), obs=obs, shape=shape, S=S, center=[clat, clon])
json.dump(out, open(a.out, "w"), indent=1)
