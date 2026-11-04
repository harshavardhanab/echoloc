"""Blind-locate a live capture with a cached z table (real clock and a wrong-clock null).

python run_table.py lhr [--half-km 7] [--step 200]

The search box is centred 3 km NE of the cam (truth, or poll centre when unknown) so the answer
is not handed in. Stream latency (5-60 s) is fitted as the clock offset. Tables are saved to
data/table_<cam>.npz so scoring rules can be re-run offline with rescore.py.
"""
import argparse
import json
import time

import numpy as np

import adsb
from locate import Locator
from physics import Frame
from spec import LogSpec, load_audio

ap = argparse.ArgumentParser()
ap.add_argument("cam")
ap.add_argument("--half-km", type=float, default=7)
ap.add_argument("--step", type=float, default=200)
a = ap.parse_args()
cam = json.load(open("cams.json"))[a.cam]
meta = json.load(open(f"data/live_{a.cam}.meta.json"))
ref = cam["truth"] or cam["at"]
clat = ref[0] + 2.1 / 111.0
clon = ref[1] + 2.1 / (111.0 * np.cos(np.radians(ref[0])))
frame = Frame(clat, clon)
elev = adsb.ground_elevation(clat, clon)
tracks = adsb.load_adsblol_log(f"data/live_{a.cam}.jsonl", ground_alt_m=elev)
spec = LogSpec(load_audio(f"data/live_{a.cam}.wav"))
loc = Locator(spec, tracks, frame, meta["wall_start"] - 30)
cal = loc.calibrate(a.half_km * 1000, elev + 1.5)
if loc.cal is None:
    raise SystemExit(f"{a.cam}: only {len(cal)} wrong-clock noise samples; too few to calibrate "
                     "(record longer, or poll ADS-B for ~15 min either side of the audio)")
print(f"{a.cam}: {len(loc.pieces)} track pieces; noise z p50 {np.median(cal):.2f} p99 {np.percentile(cal, 99):.2f}")
offsets = list(np.arange(-36, 36.1, 2.0))
obs, shape = loc.grid(0, 0, a.half_km * 1000, a.step, elev + 1.5)
t = time.time()
real = loc.table_parallel(obs, offsets)
null = loc.table_parallel(obs, [o + 397 for o in offsets])
print(f"tables: {len(real['z'])} + {len(null['z'])} entries in {time.time() - t:.0f}s")
truth = list(frame.to_xy(*cam["truth"])) if cam["truth"] else None
np.savez(f"data/table_{a.cam}.npz", obs=obs, shape=shape, offsets=offsets, cal=cal, center=[clat, clon],
         truth=truth if truth else [np.nan, np.nan], piece_callsign=[p[0]["callsign"] for p in loc.pieces],
         **{f"real_{k}": v for k, v in real.items()}, **{f"null_{k}": v for k, v in null.items()})
