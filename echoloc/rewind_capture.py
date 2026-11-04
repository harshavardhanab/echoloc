"""Download a past window of a YouTube live stream's audio, with the stream's own wall clock.

python rewind_capture.py YGaF2La0A9o --from 2026-11-02T15:00:00Z --hours 3 --name swfl/swfl_1102
python rewind_capture.py --rebuild --name swfl/swfl_1102     # redo the WAV from a saved .m4a

A 24/7 YouTube live stream keeps about the last 5 days of 5 s DASH segments. Segment N is fetched
with ?sq=N, and every segment carries the time YouTube ingested it (Ingestion-Walltime-Us). We
locate the segment for --from by interpolating from the newest one, correct with a couple of
probes, download the range in parallel, and decode to data/<name>.wav (8 kHz mono).

Timeline: the segments' media timestamps are continuous, but when the encoder drops audio the
ingestion stamps step ahead of them (seconds at a time; ~9 s over 3 h on the SWFL cam). Plain
concatenation would then drift from wall time. We estimate the stamp-minus-media offset per
segment with a rolling median (robust to ingestion jitter), and insert silence wherever it steps
up, so that in the WAV  wall time = t0 + audio seconds  (residual in meta "timeline_resid_s").

data/<name>.meta.json holds t0 (unix s of the first sample), every segment's sequence number and
ingestion stamp, and the timeline: [audio_s, wall_unix] at each segment start. The stamp is
YouTube's ingestion time; the encoder upstream adds a few seconds of constant latency.
"""
import argparse
import json
import re
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.io import wavfile

STAMP = re.compile(rb"Ingestion-Walltime-Us: (\d+)")
SEG_S = 5.0
SR = 8000
MEDIAN_SEGS = 13     # rolling-median window for the offset (65 s)
MIN_PAD_S = 0.2      # smaller offset steps are left as residual


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _boxes(b, start, end):
    off = start
    while off + 8 <= end:
        size, typ = struct.unpack(">I4s", b[off:off + 8])
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", b[off + 8:off + 16])[0], 16
        elif size == 0:
            size = end - off
        yield typ, off + hdr, off + size
        off += size


def _find(b, path, start, end):
    for typ, a, z in _boxes(b, start, end):
        if typ == path[0]:
            if len(path) == 1:
                return a
            r = _find(b, path[1:], a, z)
            if r is not None:
                return r
    return None


def media_times(b):
    """[(media start s, media duration s)] for every fragment in fMP4 bytes (one or many segments)."""
    out, scale = [], None
    for typ, a, z in _boxes(b, 0, len(b)):
        if typ == b"moov":
            p = _find(b, [b"trak", b"mdia", b"mdhd"], a, z)
            scale = struct.unpack(">I", b[p + 12:p + 16] if b[p] == 0 else b[p + 20:p + 24])[0]
        elif typ == b"moof":
            p = _find(b, [b"traf", b"tfdt"], a, z)
            base = struct.unpack(">I", b[p + 4:p + 8])[0] if b[p] == 0 else struct.unpack(">Q", b[p + 4:p + 12])[0]
            p = _find(b, [b"traf", b"tfhd"], a, z)
            fl = int.from_bytes(b[p + 1:p + 4], "big")
            q = p + 8 + (8 if fl & 1 else 0) + (4 if fl & 2 else 0)
            default = struct.unpack(">I", b[q:q + 4])[0] if fl & 8 else 0
            p = _find(b, [b"traf", b"trun"], a, z)
            fl = int.from_bytes(b[p + 1:p + 4], "big")
            n = struct.unpack(">I", b[p + 4:p + 8])[0]
            q = p + 8 + (4 if fl & 1 else 0) + (4 if fl & 4 else 0)
            if fl & 0x100:
                step = 4 * sum(bool(fl & k) for k in (0x100, 0x200, 0x400, 0x800))
                dur = sum(struct.unpack(">I", b[q + i * step:q + i * step + 4])[0] for i in range(n))
            else:
                dur = n * default
            out.append((base / scale, dur / scale))
    return out


def build_wav(m4a: Path, wav: Path, stamps):
    """Decode m4a to 8 kHz mono and splice in silence so audio time tracks the stamps.
    stamps: ingestion stamp (unix s) per fragment, in file order. Returns timeline info."""
    frags = np.array(media_times(m4a.read_bytes()))
    if len(frags) != len(stamps):
        raise SystemExit(f"{len(frags)} fragments in {m4a} but {len(stamps)} stamps")
    media = frags[:, 0] - frags[0, 0]                      # jumps where a segment is missing
    cuts = np.rint(np.r_[0.0, np.cumsum(frags[:, 1])] * SR).astype(int)
    stamps = np.asarray(stamps, float)
    raw = stamps - stamps[0] - media                       # dropouts step this up; jitter is noise
    h = MEDIAN_SEGS // 2
    padded = np.pad(raw, h, mode="edge")
    smooth = np.array([np.median(padded[i:i + MEDIAN_SEGS]) for i in range(len(raw))])
    t0 = stamps[0] + smooth[0]
    off = np.maximum.accumulate(smooth - smooth[0])         # lost audio only ever adds wall time
    target = media + off                                   # where each fragment belongs, audio s

    pcm = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(m4a), "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(pcm, np.int16)
    cuts[-1] = len(x)                                      # absorb decoder rounding at the end
    parts, starts, end, inserted = [], [], 0, 0
    for i in range(len(frags)):
        want = int(round(target[i] * SR))
        if want - end >= MIN_PAD_S * SR:
            parts.append(np.zeros(want - end, np.int16))
            inserted += want - end
            end = want
        starts.append(end / SR)
        seg = x[cuts[i]:cuts[i + 1]]
        parts.append(seg)
        end += len(seg)
    wavfile.write(wav, SR, np.concatenate(parts))
    starts = np.array(starts)
    resid = stamps - (t0 + starts)
    return {"t0": float(t0), "inserted_s": round(inserted / SR, 2), "duration_s": round(end / SR, 2),
            "timeline": [[round(float(a), 3), round(float(t0 + a), 3)] for a in starts],
            "timeline_resid_s": [round(float(v), 2) for v in np.percentile(resid, [5, 50, 95])]}


def capture(a):
    import requests

    t_from = datetime.fromisoformat(a.start.replace("Z", "+00:00")).timestamp()
    t_to = t_from + a.hours * 3600
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
    missing = [sq0 + i for i, (b, s) in enumerate(segs) if b is None or s is None]
    kept = [(sq0 + i, b, s) for i, (b, s) in enumerate(segs) if b is not None and s is not None]
    print(f"downloaded {len(kept)}/{n}; missing or unstamped {len(missing)}")
    if missing:
        print("  (missing segments are bridged by the stamp-based timeline as silence)")
    return fmt["format_id"], kept, missing


ap = argparse.ArgumentParser()
ap.add_argument("video_id", nargs="?")
ap.add_argument("--from", dest="start", help="UTC start, ISO-8601")
ap.add_argument("--hours", type=float, default=3)
ap.add_argument("--name", required=True, help="output path under data/, e.g. swfl/swfl_1102")
ap.add_argument("--itag", default="140/139", help="audio format preference")
ap.add_argument("--workers", type=int, default=8)
ap.add_argument("--rebuild", action="store_true", help="redo the WAV and timeline from data/<name>.m4a")
a = ap.parse_args()

out = Path("data") / a.name
out.parent.mkdir(parents=True, exist_ok=True)
m4a, wav, meta_path = out.with_suffix(".m4a"), out.with_suffix(".wav"), out.with_suffix(".meta.json")

if a.rebuild:
    meta = json.load(open(meta_path))
else:
    if not (a.video_id and a.start):
        ap.error("video_id and --from are required unless --rebuild")
    itag, kept, missing = capture(a)
    with open(m4a, "wb") as fh:
        for _, b, _ in kept:
            fh.write(b)
    meta = {"video_id": a.video_id, "itag": itag, "segments": [(q, s) for q, _, s in kept], "missing": missing}

tl = build_wav(m4a, wav, [s for _, s in meta["segments"]])
meta.update(tl)
meta.pop("gaps", None)
json.dump(meta, open(meta_path, "w"))
print(f"wrote {wav}: {tl['duration_s']:.1f} s, t0 {utc(tl['t0'])} UTC, {tl['inserted_s']:.1f} s of silence "
      f"inserted at dropouts; stamp residual p5/p50/p95 {tl['timeline_resid_s']} s")
