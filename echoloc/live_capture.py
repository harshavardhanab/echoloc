"""Smoking-gun test: record several live webcams' audio while logging live ADS-B around each.

python live_capture.py --cams mdw,lhr,lis --minutes 25

Cams come from cams.json ({name: {id, at:[lat,lon], feed}}). Cams sharing a feed share one
rate-limited request stream. Writes data/live_<name>.wav, data/live_<name>.jsonl and
data/live_<name>.meta.json with the wall-clock time ffmpeg was started. Live streams lag real
time by an unknown few-to-tens of seconds, so run_live.py fits that lag as the clock offset.
"""
import argparse
import json
import subprocess
import threading
import time

import adsb

GAP = {"adsb.lol": 5.0, "adsb.fi": 1.1}   # seconds between requests per feed (observed limits)

ap = argparse.ArgumentParser()
ap.add_argument("--cams", required=True)
ap.add_argument("--minutes", type=float, default=20)
ap.add_argument("--radius-nm", type=float, default=12)
a = ap.parse_args()
cams = json.load(open("cams.json"))
names = a.cams.split(",")
secs = a.minutes * 60

feeds = {}
for n in names:
    feeds.setdefault(cams[n].get("feed", "adsb.lol"), []).append(
        (*cams[n]["at"], a.radius_nm, f"data/live_{n}.jsonl"))
pollers = [threading.Thread(target=adsb.poll_adsblol, args=(sites, secs + 90, GAP[f], f), daemon=True)
           for f, sites in feeds.items()]
for p in pollers:
    p.start()
procs = []
for n in names:
    url = f"https://www.youtube.com/watch?v={cams[n]['id']}"
    stream = subprocess.run(["../.venv/bin/yt-dlp", "--js-runtimes", "node", "-g", "-f", "234/bestaudio/best", url],
                            capture_output=True, text=True, check=True).stdout.split()[0]
    # -nostdin: a detached ffmpeg that touches the terminal gets stopped (SIGTTIN)
    procs.append(subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", stream, "-t", str(secs),
                                   "-vn", "-ac", "1", "-ar", "8000", f"data/live_{n}.wav"],
                                  stdin=subprocess.DEVNULL))
    # ffmpeg opens the live edge; the first sample is roughly "now" minus the stream latency
    json.dump({"url": url, "at": cams[n]["at"], "wall_start": time.time()},
              open(f"data/live_{n}.meta.json", "w"))
    print("recording", n, flush=True)
for p in procs:
    p.wait()
for p in pollers:
    p.join(timeout=120)
print("done", flush=True)
