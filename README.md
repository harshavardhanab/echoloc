# echoloc

Locate an audio recording from the Doppler curves of the aircraft you can hear in it, and test
how well that works on recordings whose location is known.

A passing aircraft's engine tone falls in pitch as it goes by. The exact shape of that curve
depends on where the listener stood relative to the aircraft's real path. Given ADS-B tracks for
every flight nearby, each candidate spot on the ground predicts a different set of curves, and the
recording picks out the spot (and the clock offset) that explains them best.

The method is from colsto's video
[*"This background sound leaks your exact location"*](https://www.youtube.com/watch?v=UytYrhYLOKI).
This repo is an independent test harness for it: a synthetic control first, then live YouTube
webcams recorded alongside live ADS-B.

## Results

Full write-up: [echoloc/RESULTS.md](echoloc/RESULTS.md).

| Test | Result |
|---|---|
| Synthetic control: rendered passes from real tracks, hidden point | located to **21–34 m** |
| Heathrow webcam, 25 min live, blind 14 × 14 km search | best guess **6.1 km** off; true spot ranked 113–226 of 5041 |
| Midway webcam, 25 min live, blind | best guess **1.3–2.3 km** off; true spot ranked ~120 of 5041 |
| Lisbon webcam, 25 min live, blind | a deliberately wrong clock scored higher than the real one |
| SWFL eagle cam (prop traffic), 3 h rewind, dopplertrack pair search | no supported location: best guess 14.7 km off, rejected by the timestamp check; no non-jet pass measured within 2 km in 3 h |

Verdict: **it confirms a suspected location but does not find an unknown one** from 25 minutes
of airport-webcam audio. At the true spots the matched filter clearly picks out individual flights
(e.g. three Heathrow departures at ~400 m, well above the noise ceiling), but 3–5 audible passes
along the same runway corridors are not enough to stand out against ~186,000
(position, stream-delay) hypotheses. Every blind result is checked against a deliberately wrong
clock (+397 s); that null test is what exposed the failure. This pipeline is a modified version
of the video's approach (it lacks the video's explicit event measurements, flight-pair filtering
and silence filter), so the result is about this pipeline, not the video's method.

A 3-hour prop-traffic follow-up with dopplertrack's flyover-first pair search, which is much
closer to the video's method, also gave no supported location. No ADS-B-visible light aircraft
was measured within 2 km of the camera (the archive has coverage gaps), and the blind answer
(14.7 km off) scored no better than the wrong day's data and contradicted the stream's own
timestamps. That is an unsuccessful localization on these inputs, not evidence against the
video's full procedure.

## How it works

1. **Spectrogram** (`spec.py`): resample to 8 kHz, whitened log-frequency spectrogram so only
   changing tones stand out.
2. **Physics** (`physics.py`): emission-time radial velocity and propagation delay,
   `t_arr = t_emit + R/c`, `f_obs = f_src · c / (c + v_r)`. Working in ln f puts every harmonic
   on one curve.
3. **Tracks** (`adsb.py`): live polling of adsb.lol / adsb.fi into a JSONL log, or adsb.aero
   history. Heights from GNSS altitude minus an estimated geoid height.
4. **Search** (`locate.py`): for every grid point and every flight within range, lay the modelled
   curve on the spectrogram, sweep the unknown source tone f0, and z-score the curve against all
   other f0. All events share one clock offset. Scores are calibrated against the noise seen at
   deliberately wrong clocks; a flight that should have been heard (CPA < 1.5 km) but wasn't
   costs a little.
5. **Tables and rescoring** (`run_table.py`, `rescore.py`): per-(point, offset, flight) scores
   are cached once (about 10–18 min per cam on 9 processes), so scoring rules can be compared in
   seconds.

## Usage

Requires Python 3.12+, `ffmpeg`, and Node.js (used by yt-dlp for YouTube).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd echoloc
```

Synthetic control:

```bash
../.venv/bin/python run_synthetic.py --truth 51.4790,-0.3330 --start 2026-11-02T14:00:00Z --dur 600
```

Live webcams (cams are defined in `cams.json`):

```bash
../.venv/bin/python live_capture.py --cams mdw,lhr,lis --minutes 25
../.venv/bin/python run_table.py lhr
../.venv/bin/python rescore.py lhr mdw lis --topk 3
```

Past audio from a 24/7 YouTube live stream (up to ~5 days back, with exact wall-clock stamps):

```bash
../.venv/bin/python rewind_capture.py YGaF2La0A9o --from 2026-11-02T15:00:00Z --hours 3 --name swfl/swfl_1102
../.venv/bin/python rewind_capture.py --rebuild --name swfl/swfl_1102   # redo the WAV from the saved .m4a
```

The WAV is placed on the stream's wall clock: wall time = `t0` + audio seconds, with silence
inserted where the encoder dropped audio (`data/<name>.meta.json` holds `t0` and the timeline).

Your own recording:

```bash
../.venv/bin/python run_clip.py --audio clip.m4a --start 2026-11-02T14:03:10Z --region 51.48,-0.35 \
    --search-km 10 --clock-err 30 --adsblol-log data/live.jsonl
```

Outputs go to `echoloc/data/`, which is not committed.

## Files

| File | Purpose |
|---|---|
| `echoloc/physics.py` | Local frame, Doppler and propagation model |
| `echoloc/spec.py` | Audio loading and whitened log-frequency spectrogram |
| `echoloc/synth.py` | Renders what a microphone at a point would hear from real tracks |
| `echoloc/adsb.py` | ADS-B sources: live polling, poll-log loader, adsb.aero history, ground elevation |
| `echoloc/locate.py` | Matched-filter grid search, noise calibration, score tables |
| `echoloc/run_synthetic.py` | Synthetic control experiment |
| `echoloc/live_capture.py` | Records webcam audio and ADS-B side by side |
| `echoloc/rewind_capture.py` | Pulls a past window of a YouTube live stream's audio onto a wall-clock timeline (silence where the encoder dropped audio) |
| `echoloc/check_passes.py` | Detected flyovers vs ADS-B passes at a known spot, with a slid-clock null (runs in dopplertrack's env) |
| `echoloc/poll_cams.py` | Standalone ADS-B poller (superseded by `live_capture.py`) |
| `echoloc/run_clip.py` | Locates one recording |
| `echoloc/run_live.py` | Runs `run_clip.py` on each live capture |
| `echoloc/run_table.py` | Builds the cached score table for one cam, with a wrong-clock null |
| `echoloc/rescore.py` | Scores cached tables under different rules |
| `echoloc/cams.json` | Webcam list with known positions where available |

## Related work

- [Hexay/dopplerprint](https://github.com/Hexay/dopplerprint): Rust; prop and helicopter
  flyovers fitted first, then matched. Median 207 m on 40 Livermore recordings with the start time
  and flyover times supplied.
- [mjshiggins/dopplertrack](https://github.com/mjshiggins/dopplertrack): Python; detects
  flyovers, then searches flight pairs. 43 m on colsto's Oyster Bay clip and 592 m on a 12-hour
  eagle-cam stream near a flight school.

Both drop or down-rank jets and work from detected flyovers rather than scoring every flight at
every point. That may be why they succeed where this repo's airport-webcam test did not. A
3-hour run of dopplertrack on a prop-traffic cam also gave no supported location, with no
non-jet ADS-B approach measured within 2 km (see [RESULTS.md](echoloc/RESULTS.md)); its own
eagle-cam success used 12 h.

## Responsible use

This can place a recording, and so the person who made it, on a map. Use it on your own
recordings, on material published for this purpose, or with the recordist's consent.

## Data

- ADS-B: [adsb.lol](https://adsb.lol) (ODbL 1.0), [adsb.fi](https://adsb.fi) open data,
  [adsb.aero](https://adsb.aero).
- Elevation: [OpenTopoData](https://www.opentopodata.org/) SRTM 30 m.
- Webcam audio is fetched from public YouTube live streams at run time and is not redistributed.
