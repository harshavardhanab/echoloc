"""Synthetic control: render what a microphone at a known point would hear from real tracks."""
import numpy as np

from physics import doppler, resample_track
from spec import SR


def render(tracks, frame, obs, t_start, dur, c=340.0, seed=0, snr_db=-12.0, r_max=8000.0,
           decoys=4):
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    ts = t_start + np.arange(n) / SR
    y = np.zeros(n)
    used = []
    for tr in tracks:
        for tt, P, V in resample_track(tr, frame):
            ta, lnr, R = doppler(tt, P, V, obs[None, :], c)
            ta, lnr, R = ta[0], lnr[0], R[0]
            if R.min() > r_max or ta[-1] < ts[0] or ta[0] > ts[-1]:
                continue
            ok = (ts >= ta[0]) & (ts <= ta[-1])
            lr = np.interp(ts[ok], ta, lnr)
            rr = np.interp(ts[ok], ta, R)
            prop = tr.get("cat") in ("A1", "A2", "B4", "A7") or rng.random() < 0.3
            f0 = rng.uniform(70, 140) if prop else rng.uniform(800, 1800)
            amp = 300.0 / rr * np.exp(-rr / 4000.0)
            ph = 2 * np.pi * np.cumsum(f0 * np.exp(lr)) / SR
            harm = (1, 2, 3, 4) if prop else (1, 2)
            for h in harm:
                y[ok] += amp / h * np.sin(h * ph)
            used.append({"callsign": tr["callsign"], "type": tr["type"], "f0": round(f0, 1),
                         "cpa_m": round(float(R.min()))})
    # nuisance: stationary hum, random pitch sweeps (cars, birds), pink-ish noise
    y += 0.05 * np.sin(2 * np.pi * 60 * ts) + 0.03 * np.sin(2 * np.pi * 120 * ts)
    for _ in range(decoys):
        a = rng.uniform(0, dur - 20)
        m = (ts - ts[0] >= a) & (ts - ts[0] <= a + rng.uniform(5, 20))
        f = rng.uniform(200, 900) * np.exp(np.linspace(0, rng.uniform(-0.3, 0.3), m.sum()))
        y[m] += 0.05 * np.sin(2 * np.pi * np.cumsum(f) / SR)
    noise = np.cumsum(rng.standard_normal(n)) * 0.02
    noise -= np.convolve(noise, np.ones(SR) / SR, mode="same")
    noise += rng.standard_normal(n)
    sig_p = np.mean(y ** 2) + 1e-12
    noise *= np.sqrt(sig_p / np.mean(noise ** 2) / 10 ** (snr_db / 10))
    return (y + noise).astype(np.float32), used
