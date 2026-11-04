"""Synthetic control: real ADS-B tracks, rendered audio at a hidden point, blind search.

python run_synthetic.py --truth 51.4790,-0.3330 --start 2026-11-02T14:00:00Z --dur 600
"""
import argparse
import json
import time
from datetime import datetime

import numpy as np

import adsb
from locate import Locator
from physics import Frame
from spec import LogSpec
from synth import render


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


ap = argparse.ArgumentParser()
ap.add_argument("--truth", required=True)
ap.add_argument("--start", required=True)
ap.add_argument("--dur", type=float, default=600)
ap.add_argument("--search-km", type=float, default=10, help="half-width of search box")
ap.add_argument("--clock-err", type=float, default=0.0, help="true clock error (s) the search must find")
ap.add_argument("--snr", type=float, default=-12)
ap.add_argument("--seed", type=int, default=1)
a = ap.parse_args()

lat, lon = map(float, a.truth.split(","))
t0 = ts(a.start)
rng = np.random.default_rng(a.seed)
# search box centre is NOT the truth: shift it by up to 60% of the half-width
clat = lat + rng.uniform(-0.6, 0.6) * a.search_km / 111.0
clon = lon + rng.uniform(-0.6, 0.6) * a.search_km / (111.0 * np.cos(np.radians(lat)))
frame = Frame(clat, clon)

tracks = adsb.fetch_aero(clat, clon, (a.search_km + 8) * 1000, t0 - 300, t0 + a.dur + 300,
                         cache=f"data/aero_{clat:.3f}_{clon:.3f}_{int(t0)}_{int(a.dur)}.json")
elev = adsb.ground_elevation(lat, lon)
elev_c = adsb.ground_elevation(clat, clon)
tx, ty = frame.to_xy(lat, lon)
truth = np.array([float(tx), float(ty), elev + 1.5])
x, used = render(tracks, frame, truth, t0 + a.clock_err, a.dur, seed=a.seed, snr_db=a.snr)
print(f"{len(tracks)} track pieces in region; audible in clip: {len(used)}")
for u in used:
    print("  ", u)

t = time.time()
spec = LogSpec(x)
loc = Locator(spec, tracks, frame, t0)
offsets = list(np.arange(-6, 6.1, 2.0))
res = loc.search(0, 0, a.search_km * 1000, elev_c + 1.5, offsets)
print(f"search took {time.time() - t:.0f}s")
null = loc.search(0, 0, a.search_km * 1000, elev_c + 1.5, [o + 397 for o in offsets], top=1)
null_best = null["candidates"][0][0] if null["candidates"] else 0.0
best = res["candidates"][0][0] if res["candidates"] else 0.0
print(f"null (clock deliberately wrong by 397 s) best score: {null_best:.1f}  -> margin {best - null_best:+.1f}")
truth_S = loc.score(truth[None, :], offsets)[0]
print(f"score at truth: {truth_S.max():.1f} (offset {offsets[int(truth_S.argmax())]})")
out = []
for s, q, off in res["candidates"]:
    plat, plon = frame.to_latlon(q[0], q[1])
    err = np.hypot(q[0] - truth[0], q[1] - truth[1])
    print(f"  cand score {s:6.1f}  {float(plat):.5f},{float(plon):.5f}  offset {off:+.0f}s  error {err:6.0f} m")
    out.append({"score": s, "lat": float(plat), "lon": float(plon), "offset": off, "err_m": float(err)})
obs, shape, S = res["coarse"]
np.savez(f"data/synth_{a.seed}.npz", obs=obs, shape=shape, S=S, truth=truth, center=[clat, clon])
json.dump({"truth": [lat, lon], "center": [clat, clon], "audible": used, "candidates": out},
          open(f"data/synth_{a.seed}.json", "w"), indent=1)
