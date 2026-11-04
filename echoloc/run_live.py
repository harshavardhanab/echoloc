"""Blind-locate each live capture. The search box is centred 3 km from the cam (or its ADS-B
poll centre) so the answer is not handed in; stream latency is fitted as the clock offset.

python run_live.py mdw lhr lis
"""
import json
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np

cams = json.load(open("cams.json"))
for n in sys.argv[1:]:
    c, meta = cams[n], json.load(open(f"data/live_{n}.meta.json"))
    ref = c["truth"] or c["at"]
    # 3 km north-east of the reference point
    clat = ref[0] + 2.1 / 111.0
    clon = ref[1] + 2.1 / (111.0 * np.cos(np.radians(ref[0])))
    # YouTube live lag ~5-60 s: centre the guess at 30 s behind wall clock, search +-36 s
    start = datetime.fromtimestamp(meta["wall_start"] - 30, timezone.utc).isoformat()
    cmd = [sys.executable, "run_clip.py", "--audio", f"data/live_{n}.wav", "--start", start,
           "--region", f"{clat},{clon}", "--search-km", "8", "--clock-err", "36",
           "--adsblol-log", f"data/live_{n}.jsonl", "--out", f"data/live_{n}_result.json"]
    if c["truth"]:
        cmd += ["--truth", f"{c['truth'][0]},{c['truth'][1]}"]
    print("==", n, c["note"], flush=True)
    subprocess.run(cmd)
