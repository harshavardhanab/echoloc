"""Grid search: where (and at what clock offset) do real flights explain the spectrogram?

For each candidate point and each aircraft that passes within audible range, model the
Doppler curve that point would have heard (emission-time radial velocity), lay it on the
spectrogram and score the brightness along it, sweeping the unknown source tone f0.
Scores are z-scores against the same curve at every other f0 (so a curve shape that
matches nothing scores ~ the noise maximum). All events share one clock offset (locked).
"""
import numpy as np

from physics import doppler, resample_track

Z_NOISE = 3.5     # typical max-over-f0 z for a curve that matches nothing (synthetic noise)
Z_FLOOR = -2.0    # a single missed aircraft can cost at most this much (occlusion / quiet jets)
CAL_FLOOR = -1.0  # same cap, in calibrated units (log10 of tail-probability ratio)


def curve_z(spec, ta_rel, lnr, frame_step=4):
    """z-score of the best f0 for one curve. ta_rel: arrival times (audio seconds)."""
    t = spec.t[::frame_step]
    ok = (t >= ta_rel[0]) & (t <= ta_rel[-1])
    if ok.sum() < 8:
        return None
    rows = np.flatnonzero(ok) * frame_step
    k = np.interp(spec.t[rows], ta_rel, lnr) / spec.dlog
    k = np.rint(k - k.min()).astype(int)
    F = spec.W.shape[1] - k.max()
    if F < 50:
        return None
    cols = np.arange(F)[None, :] + k[:, None]
    vec = spec.W[rows[:, None], cols].mean(axis=0)
    med = np.median(vec)
    mad = np.median(np.abs(vec - med)) * 1.4826 + 1e-9
    j = int(np.argmax(vec))
    return (vec[j] - med) / mad, j


class Locator:
    def __init__(self, spec, tracks, frame, t_audio0, c=340.0, r_hear=8000.0, r_win=4000.0,
                 max_half_s=60.0, min_swing=0.0):
        self.spec, self.frame, self.t0, self.c = spec, frame, t_audio0, c
        self.r_hear, self.r_win, self.max_half = r_hear, r_win, max_half_s
        # curves whose ln-frequency barely changes carry no position information but still
        # match any steady tone; skip events whose modelled Doppler swing is below this
        self.min_swing = min_swing
        self.cal = None
        self.pieces = []
        for tr in tracks:
            for tt, P, V in resample_track(tr, frame):
                self.pieces.append((tr, tt, P, V))
        self.dur = spec.t[-1]

    def _events(self, obs, offsets):
        """Yield (piece index, [M] mask of points that hear it, ta[M,N], lnr, R)."""
        lo = self.t0 + min(offsets) - 120
        hi = self.t0 + max(offsets) + self.dur + 120
        for i, (tr, tt, P, V) in enumerate(self.pieces):
            m = (tt >= lo - 60) & (tt <= hi)
            if m.sum() < 10:
                continue
            ta, lnr, R = doppler(tt[m], P[m], V[m], obs, self.c)
            hear = R.min(axis=1) < self.r_hear
            if hear.any():
                yield i, hear, ta, lnr, R

    def calibrate(self, half, z_obs, wrong=(503.0, -611.0, 733.0, -859.0), n=60, seed=0):
        """Learn this recording's noise distribution of curve z from deliberately wrong clocks.

        Real audio is full of tones, so a curve that matches nothing still scores z ~ 3-4 with a
        heavy tail; afterwards each event scores log10(0.1 / p_tail(z)): positive only above the
        noise 90th percentile, and an aircraft that should be heard but is not costs a little.
        """
        rng = np.random.default_rng(seed)
        pts = np.c_[rng.uniform(-half, half, n), rng.uniform(-half, half, n), np.full(n, z_obs)]
        _, info = self.score(pts, list(wrong), detail=True)
        self.cal = np.sort([e["z"] for p in info for es in p for e in es])
        return self.cal

    def _contrib(self, z):
        if self.cal is None:
            return max(z - Z_NOISE, Z_FLOOR)
        n = len(self.cal)
        p = (n - np.searchsorted(self.cal, z, side="left") + 1) / (n + 1)
        return max(np.log10(0.1 / p), CAL_FLOOR)

    def table(self, obs, offsets):
        """Every (point, offset, event) z in one pass: arrays m, o, z, cpa_m, piece.
        Scoring rules can then be applied offline (see score_table)."""
        rows = []
        self._loop(obs, offsets, lambda m, o, i, z, cpa, cpa_rel: rows.append((m, o, z, cpa, i)))
        a = np.array(rows, float).reshape(-1, 5)
        return {"m": a[:, 0].astype(int), "o": a[:, 1].astype(int), "z": a[:, 2], "cpa": a[:, 3],
                "piece": a[:, 4].astype(int), "M": len(obs), "O": len(offsets)}

    def table_parallel(self, obs, offsets, procs=9):
        import multiprocessing as mp
        chunks = np.array_split(np.arange(len(obs)), procs)
        global _LOC
        _LOC = self
        with mp.get_context("fork").Pool(procs) as pool:
            parts = pool.starmap(_table_chunk, [(obs[c], offsets) for c in chunks])
        out = {k: np.concatenate([p[k] + (c[0] if k == "m" else 0) for p, c in zip(parts, chunks)])
               for k in ("m", "o", "z", "cpa", "piece")}
        out.update(M=len(obs), O=len(offsets))
        return out

    def score_table(self, tab, quiet_m=1500.0):
        """Calibrated score per (point, offset). A miss only costs when the aircraft passed
        within quiet_m (farther ones are often inaudible over wind/traffic)."""
        n = len(self.cal)
        p = (n - np.searchsorted(self.cal, tab["z"], side="left") + 1) / (n + 1)
        c = np.log10(0.1 / p)
        c = np.where(c >= 0, c, np.where(tab["cpa"] < quiet_m, np.maximum(c, CAL_FLOOR), 0.0))
        S = np.zeros(tab["M"] * tab["O"])
        np.add.at(S, tab["m"] * tab["O"] + tab["o"], c)
        return S.reshape(tab["M"], tab["O"])

    def score(self, obs, offsets, detail=False):
        """obs [M,3] local ENU. Returns S[M, len(offsets)] (and per-event detail)."""
        M = len(obs)
        S = np.zeros((M, len(offsets)))
        info = [[[] for _ in offsets] for _ in range(M)] if detail else None

        def add(m, o, i, z, cpa, cpa_rel):
            S[m, o] += self._contrib(z)
            if detail:
                info[m][o].append({"piece": i, "callsign": self.pieces[i][0]["callsign"],
                                   "type": self.pieces[i][0]["type"], "z": round(float(z), 2),
                                   "cpa_m": round(float(cpa)), "cpa_audio_s": round(float(cpa_rel), 1)})
        self._loop(obs, offsets, add)
        return (S, info) if detail else S

    def _loop(self, obs, offsets, emit):
        for i, hear, ta, lnr, R in self._events(obs, offsets):
            for m in np.flatnonzero(hear):
                jc = int(np.argmin(R[m]))
                win = R[m] < max(self.r_win, 1.6 * R[m, jc])
                # contiguous window around CPA, capped
                a = jc
                while a > 0 and win[a - 1] and ta[m, jc] - ta[m, a - 1] < self.max_half:
                    a -= 1
                b = jc
                while b < len(win) - 1 and win[b + 1] and ta[m, b + 1] - ta[m, jc] < self.max_half:
                    b += 1
                if b - a < 20 or np.ptp(lnr[m, a:b + 1]) < self.min_swing:
                    continue
                for o, off in enumerate(offsets):
                    rel = ta[m, a:b + 1] - (self.t0 + off)
                    cpa_rel = ta[m, jc] - (self.t0 + off)
                    if cpa_rel < 5 or cpa_rel > self.dur - 5:
                        continue           # CPA not inside the recording
                    r = curve_z(self.spec, rel, lnr[m, a:b + 1])
                    if r is None:
                        continue
                    emit(m, o, i, r[0], R[m, jc], cpa_rel)

    def grid(self, cx, cy, half, step, z_obs):
        xs = np.arange(cx - half, cx + half + 1e-6, step)
        ys = np.arange(cy - half, cy + half + 1e-6, step)
        X, Y = np.meshgrid(xs, ys)
        obs = np.stack([X.ravel(), Y.ravel(), np.full(X.size, z_obs)], axis=1)
        return obs, X.shape

    def search(self, cx, cy, half, z_obs, offsets, coarse=400.0, fine=(100.0, 25.0), top=5):
        obs, shape = self.grid(cx, cy, half, coarse, z_obs)
        S = self.score(obs, offsets)
        best = S.max(axis=1)
        result = {"coarse": (obs, shape, S)}
        # refine around the top distinct coarse cells
        order = np.argsort(-best)
        seeds = []
        for idx in order:
            if best[idx] <= 0:
                break
            p = obs[idx, :2]
            if all(np.hypot(*(p - q)) > 2 * coarse for q in seeds):
                seeds.append(p)
            if len(seeds) >= top:
                break
        cands = []
        for p in seeds:
            q, half_r = p, coarse
            for step in fine:
                o2, _ = self.grid(q[0], q[1], half_r, step, z_obs)
                S2 = self.score(o2, offsets)
                k = np.unravel_index(np.argmax(S2), S2.shape)
                q, half_r = o2[k[0], :2], 2 * step
            cands.append((float(S2.max()), q, offsets[k[1]]))
        cands.sort(key=lambda c: -c[0])
        result["candidates"] = cands
        return result


_LOC = None


def _table_chunk(obs, offsets):
    return _LOC.table(obs, offsets)
