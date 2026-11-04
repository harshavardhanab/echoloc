"""ADS-B track sources.

- adsb.aero  : historical 4D trajectories (2025-04-28 .. today), no key.
- adsb.lol   : live positions; poll and append to a JSONL log, then load as tracks.

A track is a dict: {id, callsign, type, cat, t (s, unix), lat, lon, alt_m (MSL)}.
"""
import json
import time
from datetime import datetime, timezone

import numpy as np
import requests

FT = 0.3048
AERO = "https://adsb.aero/api/v1"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_aero(lat, lon, radius_m, t0, t1, alt_max_ft=12000, cache=None):
    """All flights whose path enters the circle below alt_max_ft between t0 and t1."""
    if cache:
        try:
            return json.load(open(cache))
        except FileNotFoundError:
            pass
    body = {
        "match": {"trajectory_intersects": {
            "geometry": {"type": "Circle", "coordinates": [lon, lat], "radius": radius_m},
            "altitude_max": alt_max_ft,
            "time_from": iso(t0), "time_to": iso(t1)}},
        "end_date": iso(t1 + 86400), "window_days": 3,
        "limit": 1000, "include_path": True,
    }
    flights, cursor = [], None
    for _ in range(10):
        if cursor:
            body["cursor"] = cursor
        r = requests.post(f"{AERO}/query", json=body, timeout=120)
        r.raise_for_status()
        d = r.json()
        flights += d["flights"]
        cursor = d.get("cursor")
        if not cursor:
            break
    tracks = []
    for f in flights:
        corr = [p for seg in (f.get("alt_correction_ft") or []) for p in seg]
        corr_t = np.array([p[0] for p in corr]) if corr else None
        corr_v = np.array([p[1] for p in corr]) if corr else None
        for seg, ts in zip(f["path"]["coordinates"], f["timestamps"]):
            if len(seg) < 2:
                continue
            a = np.array(seg, float)
            t = np.array(ts, float)
            alt_ft = a[:, 2].copy()
            if corr_t is not None:
                i = np.searchsorted(corr_t, t, side="right") - 1
                ok = i >= 0
                alt_ft[ok] += corr_v[i[ok]]
            m = (t >= t0 - 600) & (t <= t1 + 600)
            if m.sum() < 2:
                continue
            # keep one point either side of the window so interpolation spans it
            idx = np.flatnonzero(m)
            lo, hi = max(idx[0] - 1, 0), min(idx[-1] + 2, len(t))
            tracks.append({
                "id": f["flight_id"], "callsign": (f.get("callsign") or "").strip(),
                "type": f.get("icao_type"), "cat": f.get("emitter_category"),
                "t": t[lo:hi].tolist(), "lat": a[lo:hi, 1].tolist(), "lon": a[lo:hi, 0].tolist(),
                "alt_m": (alt_ft[lo:hi] * FT).tolist(),
            })
    if cache:
        json.dump(tracks, open(cache, "w"))
    return tracks


FEEDS = {  # readsb-style point queries; {lat} {lon} {nm}
    "adsb.lol": "https://api.adsb.lol/v2/point/{lat}/{lon}/{nm}",
    "adsb.fi": "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{nm}",
}


def poll_adsblol(sites, duration_s, gap_s=1.5, feed="adsb.lol"):
    """Round-robin raw snapshots for several sites from one client of one feed.

    sites: list of (lat, lon, radius_nm, out_path). Feeds rate-limit per IP, so all sites
    share one request stream spaced gap_s apart; each HTTP 429 waits out retry-after and
    widens the gap by 25%. Snapshots are normalised to {"t", "now" (ms), "ac"}.
    """
    end = time.time() + duration_s
    fhs = [open(s[3], "a") for s in sites]
    i = 0
    while time.time() < end:
        lat, lon, rnm, _ = sites[i % len(sites)]
        t_req = time.time()
        try:
            r = requests.get(FEEDS[feed].format(lat=lat, lon=lon, nm=rnm), timeout=10)
            if r.status_code == 429:
                wait = float(r.headers.get("retry-after", 30))
                gap_s *= 1.25
                print(f"{feed} 429, backing off {wait:.0f}s, gap now {gap_s:.1f}s", flush=True)
                time.sleep(wait)
                continue
            d = r.json()
            now = d.get("now")
            if now is not None and now < 1e11:   # adsb.fi reports seconds
                now *= 1000
            ac = d.get("ac") if "ac" in d else d.get("aircraft", [])
            fhs[i % len(sites)].write(json.dumps({"t": t_req, "now": now, "ac": ac or []}) + "\n")
            fhs[i % len(sites)].flush()
            i += 1
        except Exception as e:  # keep polling through transient errors
            print(feed, "poll error", e, flush=True)
        time.sleep(max(0.0, gap_s - (time.time() - t_req)))


def load_adsblol_log(path, ground_alt_m=None, geoid_m=None):
    """Turn a poll log into tracks. Position time = now - seen_pos.

    Height: GNSS altitude (alt_geom, WGS84 ellipsoid) minus the geoid height. If geoid_m is
    None it is estimated as median(alt_geom - QNH-corrected baro) over low aircraft that report
    their altimeter setting. Aircraft on the ground (takeoff/landing roll) are placed at
    ground_alt_m when given, else dropped.
    """
    snaps = [json.loads(line) for line in open(path)]
    if geoid_m is None:
        d = [(ac["alt_geom"] - (ac["alt_baro"] + (ac["nav_qnh"] - 1013.25) * 27.3)) * FT
             for s in snaps for ac in s["ac"]
             if isinstance(ac.get("alt_baro"), (int, float)) and ac["alt_baro"] < 8000
             and ac.get("alt_geom") is not None and ac.get("nav_qnh")]
        geoid_m = float(np.median(d)) if len(d) > 20 else 0.0
        print(f"geoid estimate {geoid_m:.1f} m from {len(d)} reports")
    pts = {}
    for snap in snaps:
        now = (snap.get("now") or snap["t"] * 1000) / 1000.0
        for ac in snap["ac"]:
            if "lat" not in ac or ac.get("seen_pos", 0) > 5:
                continue
            if ac.get("alt_baro") == "ground":
                if ground_alt_m is None:
                    continue
                alt_m = ground_alt_m + 3.0
            elif ac.get("alt_geom") is not None:
                alt_m = ac["alt_geom"] * FT - geoid_m
            else:
                continue
            t = now - ac.get("seen_pos", 0.0)
            p = pts.setdefault(ac["hex"], {"meta": ac, "rows": {}})
            p["rows"][round(t, 1)] = (ac["lat"], ac["lon"], alt_m)
    tracks = []
    for hx, p in pts.items():
        rows = sorted(p["rows"].items())
        if len(rows) < 5:
            continue
        t = np.array([r[0] for r in rows])
        v = np.array([r[1] for r in rows])
        tracks.append({"id": hx, "callsign": (p["meta"].get("flight") or "").strip(),
                       "type": p["meta"].get("t"), "cat": p["meta"].get("category"),
                       "t": t.tolist(), "lat": v[:, 0].tolist(), "lon": v[:, 1].tolist(),
                       "alt_m": v[:, 2].tolist()})
    return tracks


def ground_elevation(lat, lon):
    """MSL ground elevation (m) from OpenTopoData SRTM 30 m."""
    r = requests.get(f"https://api.opentopodata.org/v1/srtm30m?locations={lat},{lon}", timeout=30)
    return float(r.json()["results"][0]["elevation"])
