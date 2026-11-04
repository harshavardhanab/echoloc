"""Download a past window of a YouTube live stream's audio, with the stream's own wall clock.

python rewind_capture.py YGaF2La0A9o --from 2026-11-02T15:00:00Z --hours 3 --name swfl

A 24/7 YouTube live stream keeps about the last 5 days of 5 s DASH segments. Segment N is fetched
with ?sq=N, and every segment carries the time YouTube ingested it (Ingestion-Walltime-Us). We
locate the segment for --from by interpolating from the newest one, correct with a couple of
probes, download the range in parallel, and decode to data/<name>.wav (8 kHz mono).

data/<name>.meta.json holds t0 (unix s of the first sample, from the first segment's stamp) and
every segment's sequence number and stamp, so gaps or clock jumps in the stream are visible.
The stamp is YouTube's ingestion time: the encoder upstream adds a few seconds of latency.
"""
import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

STAMP = re.compile(rb"Ingestion-Walltime-Us: (\d+)")
SEG_S = 5.0


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

ap = argparse.ArgumentParser()
ap.add_argument("video_id")
ap.add_argument("--from", dest="start", required=True, help="UTC start, ISO-8601")
ap.add_argument("--hours", type=float, default=3)
ap.add_argument("--name", required=True)
ap.add_argument("--itag", default="140/139", help="audio format preference")
ap.add_argument("--workers", type=int, default=8)
a = ap.parse_args()

t_from = datetime.fromisoformat(a.start.replace("Z", "+00:00")).timestamp()
t_to = t_from + a.hours * 3600
out = Path("data")

info = json.loads(subprocess.run(
    ["../.venv/bin/yt-dlp", "--js-runtimes", "node", "--live-from-start", "-j",
     f"https://www.youtube.com/watch?v={a.video_id}"],
    capture_output=True, text=True, check=True).stdout)
fmts = {f["format_id"]: f for f in info["formats"] if f.get("is_from_start")}
fmt = next(fmts[i] for i in a.itag.split("/") if i in fmts)
base, headers = fmt["url"], fmt.get("http_headers", {})
sess = requests.Session()
sess.headers.update(headers)


def get(sq, tries=4):
    for _ in range(tries):
        try:
            r = sess.get(base, params={"sq": sq}, timeout=30)
            if r.status_code == 200 and r.content:
                m = STAMP.search(r.content)
                return r.content, int(m.group(1)) / 1e6 if m else None
        except requests.RequestException:
            pass
    return None, None


head = int(sess.head(base, timeout=30).headers["X-Head-Seqnum"])
_, t_head = get(head - 1)
print(f"format {fmt['format_id']}, newest segment {head - 1} at {utc(t_head)} UTC")

# Segments are nominally 5 s but streams drop and restart; walk to the target by probing.
sq = head - 1 - int((t_head - t_from) / SEG_S)
for _ in range(6):
    _, t = get(sq)
    if t is None:
        raise SystemExit(f"segment {sq} unavailable: {a.start} is probably older than the ~5 day buffer")
    step = int(round((t_from - t) / SEG_S))
    if step == 0:
        break
    sq += step
sq0, n = sq, int(round((t_to - t_from) / SEG_S))
print(f"segments {sq0}..{sq0 + n - 1} ({n}), first stamped {utc(t)} UTC")

with ThreadPoolExecutor(a.workers) as ex:
    segs = list(ex.map(get, range(sq0, sq0 + n)))
missing = [sq0 + i for i, (b, _) in enumerate(segs) if b is None]
stamps = [(sq0 + i, s) for i, (b, s) in enumerate(segs) if b is not None]
gaps = [(q1, round(s1 - s0, 2)) for (q0, s0), (q1, s1) in zip(stamps, stamps[1:])
        if s0 and s1 and abs(s1 - s0 - SEG_S * (q1 - q0)) > 1.0]
print(f"downloaded {len(stamps)}/{n}; missing {len(missing)}; stamp gaps {len(gaps)}")

m4a = out / f"{a.name}.m4a"
with open(m4a, "wb") as fh:
    for b, _ in segs:
        if b is not None:
            fh.write(b)
subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(m4a), "-ac", "1", "-ar", "8000",
                str(out / f"{a.name}.wav")], check=True, stdin=subprocess.DEVNULL)
json.dump({"video_id": a.video_id, "itag": fmt["format_id"], "t0": stamps[0][1],
           "segments": stamps, "missing": missing, "gaps": gaps},
          open(out / f"{a.name}.meta.json", "w"))
print(f"wrote {out / a.name}.wav, t0 {utc(stamps[0][1])} UTC")
