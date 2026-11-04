"""Log adsb.lol around several cams from one rate-limited request stream.

python poll_cams.py --cams mdw,lhr,lis,msy --minutes 25
"""
import argparse
import json

import adsb

ap = argparse.ArgumentParser()
ap.add_argument("--cams", required=True)
ap.add_argument("--minutes", type=float, default=25)
ap.add_argument("--radius-nm", type=float, default=12)
ap.add_argument("--gap", type=float, default=1.5)
a = ap.parse_args()
cams = json.load(open("cams.json"))
sites = [(*cams[n]["at"], a.radius_nm, f"data/live_{n}.jsonl") for n in a.cams.split(",")]
adsb.poll_adsblol(sites, a.minutes * 60, a.gap)
