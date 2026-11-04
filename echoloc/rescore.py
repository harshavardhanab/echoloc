"""Apply a scoring rule to cached z tables and report blind result vs truth and null.

python rescore.py lhr mdw lis [--quiet 1500] [--topk 3]
"""
import argparse

import numpy as np

from physics import Frame

ap = argparse.ArgumentParser()
ap.add_argument("cams", nargs="+")
ap.add_argument("--quiet", type=float, default=1500, help="misses cost only if CPA < this (m)")
ap.add_argument("--floor", type=float, default=-1.0)
ap.add_argument("--topk", type=int, default=0, help="keep only the k strongest positive events per guess (0 = all)")
a = ap.parse_args()


def scores(d, pre, cal):
    z, cpa = d[pre + "z"], d[pre + "cpa"]
    n = len(cal)
    p = (n - np.searchsorted(cal, z, side="left") + 1) / (n + 1)
    c = np.log10(0.1 / p)
    c = np.where(c >= 0, c, np.where(cpa < a.quiet, np.maximum(c, a.floor), 0.0))
    M, O = int(d[pre + "M"]), int(d[pre + "O"])
    key = d[pre + "m"] * O + d[pre + "o"]
    if a.topk:
        # loud close passes only: misses and weak events say little on a noisy live mic
        c = np.maximum(c, 0.0)
        order = np.lexsort((-c, key))
        k_sorted = key[order]
        rank = np.arange(len(k_sorted)) - np.searchsorted(k_sorted, k_sorted, side="left")
        keep = np.zeros(len(c), bool)
        keep[order[rank < a.topk]] = True
        c = np.where(keep, c, 0.0)
    S = np.zeros(M * O)
    np.add.at(S, key, c)
    return S.reshape(M, O)


for cam in a.cams:
    d = np.load(f"data/table_{cam}.npz")
    obs, offs, fr = d["obs"], d["offsets"], Frame(*d["center"])
    S, N = scores(d, "real_", d["cal"]), scores(d, "null_", d["cal"])
    best = S.max(axis=1)
    k = int(best.argmax())
    lat, lon = fr.to_latlon(*obs[k, :2])
    line = (f"{cam}: best {best[k]:.1f} at {float(lat):.5f},{float(lon):.5f} off {offs[S[k].argmax()]:+.0f}s"
            f" | null best {N.max():.1f} -> margin {best[k] - N.max():+.1f}")
    if not np.isnan(d["truth"][0]):
        dist = np.hypot(obs[:, 0] - d["truth"][0], obs[:, 1] - d["truth"][1])
        near = dist < 300
        kt = np.flatnonzero(near)[best[near].argmax()]
        rank = int((best > best[kt]).sum()) + 1
        line += (f" | err {dist[k]:.0f} m | best within 300 m of truth {best[kt]:.1f}"
                 f" (rank {rank}/{len(best)}, off {offs[S[kt].argmax()]:+.0f}s)")
    print(line)
    order = np.argsort(-best)
    shown = []
    for j in order:
        if all(np.hypot(*(obs[j, :2] - obs[q, :2])) > 1000 for q in shown):
            shown.append(j)
            la, lo = fr.to_latlon(*obs[j, :2])
            extra = f" err {np.hypot(*(obs[j, :2] - d['truth'])):.0f} m" if not np.isnan(d["truth"][0]) else ""
            print(f"    {best[j]:5.1f}  {float(la):.5f},{float(lo):.5f}{extra}")
        if len(shown) == 4:
            break
