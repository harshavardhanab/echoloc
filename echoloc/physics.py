"""Geometry and Doppler model.

Stationary observer, still uniform air, moving source:
    t_arrival = t_emit + R(t_emit) / c
    f_obs     = f_src * c / (c + v_radial(t_emit))     (v_radial > 0 receding)
We work in ln(f_obs / f_src), so every harmonic of an engine shares one curve.
"""
import numpy as np

R_EARTH = 6371000.0


class Frame:
    """Local tangent plane (east, north, up) around a reference point."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.k = np.cos(np.radians(lat0))

    def to_xy(self, lat, lon):
        lat, lon = np.asarray(lat, float), np.asarray(lon, float)
        return (np.radians(lon - self.lon0) * R_EARTH * self.k,
                np.radians(lat - self.lat0) * R_EARTH)

    def to_latlon(self, x, y):
        return (self.lat0 + np.degrees(np.asarray(y) / R_EARTH),
                self.lon0 + np.degrees(np.asarray(x) / (R_EARTH * self.k)))


def resample_track(track, frame, dt=0.5, max_gap=90.0, smooth_s=6.0):
    """Uniformly resampled positions/velocities in the local frame.
    Returns list of (t, P[N,3], V[N,3]) pieces split at coverage gaps."""
    t = np.asarray(track["t"], float)
    x, y = frame.to_xy(track["lat"], track["lon"])
    # curvature drop so far-away aircraft sit at the right height relative to the observer
    z = np.asarray(track["alt_m"], float) - (x ** 2 + y ** 2) / (2 * R_EARTH)
    order = np.argsort(t)
    t, x, y, z = t[order], x[order], y[order], z[order]
    keep = np.concatenate([[True], np.diff(t) > 0.05])
    t, x, y, z = t[keep], x[keep], y[keep], z[keep]
    pieces = []
    breaks = np.flatnonzero(np.diff(t) > max_gap) + 1
    for s, e in zip(np.r_[0, breaks], np.r_[breaks, len(t)]):
        if e - s < 2 or t[e - 1] - t[s] < 10:
            continue
        tt = np.arange(t[s], t[e - 1], dt)
        P = np.stack([np.interp(tt, t[s:e], v[s:e]) for v in (x, y, z)], axis=1)
        V = np.gradient(P, dt, axis=0)
        w = max(1, int(smooth_s / dt))
        if len(tt) > w:
            ker = np.ones(w) / w
            V = np.stack([np.convolve(V[:, i], ker, mode="same") for i in range(3)], axis=1)
            V[: w // 2] = V[w // 2]
            V[-(w // 2):] = V[-(w // 2) - 1]
        speed = np.linalg.norm(V[:, :2], axis=1)
        if np.median(speed) < 15 or np.max(speed) > 340:   # parked / garbage
            continue
        pieces.append((tt, P, V))
    return pieces


def doppler(tt, P, V, obs, c=340.0):
    """obs: [M,3]. Returns arrival time [M,N], ln ratio [M,N], slant range [M,N]."""
    D = P[None, :, :] - obs[:, None, :]
    R = np.linalg.norm(D, axis=2)
    vr = np.einsum("mnk,nk->mn", D, V) / R
    return tt[None, :] + R / c, np.log(c / (c + vr)), R
