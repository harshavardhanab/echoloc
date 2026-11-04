# echoloc — results (2026-11-03, prop-cam test 2026-11-04)

Method: match aircraft Doppler curves in a recording against ADS-B tracks, grid-search the
listener position and the clock offset, and compare against a deliberately wrong clock (+397 s null).

## Synthetic control
Rendered aircraft passes, known position: located to 21–34 m. Pipeline is correct.

## Live webcams (25 min of YouTube live audio + live ADS-B polled at the same time)
14 x 14 km blind search box (200 m grid, 5041 points) centred 3 km NE of the camera, stream lag
fitted over ±36 s. Noise level calibrated from wrong clocks.

| cam | blind best: error | margin over null | true spot rank (of 5041) |
|---|---|---|---|
| Heathrow (Myrtle Ave) | 6.1 km | +1.6 | 226 (113 with --topk 5) |
| Midway | 2.3 km (1.3 km with --topk 3) | +2.3 | 2755 (129 with --topk 3) |
| Lisbon | no known spot | −3.6 (null wins) | — |

Event-level evidence at the known spots is real:
- Heathrow, lag +12..16 s: TAP1359 z 10.8, SHT8U 9.9, BAW596 9.0 (noise p99 ≈ 9.2), all passing at ~400 m.
  A local 2 km search peaks 216 m from the listed spot and beats random control spots.
- Midway, lag −6 s: SWA3372 z 8.7, SWA2721 8.1 (noise p99.9 ≈ 8.0).

## Verdict
Confirms a suspected location; does not find an unknown one from 25 min of webcam audio.
Only 3–5 aircraft are clearly audible per stream, and airport traffic follows the same few
corridors, so a handful of passes constrains distance-to-track but leaves a strip of positions
along it. Against ~186k (position, lag) hypotheses that is not enough to stand out from chance.

## What might fix it
- Longer captures (2–3 h) for more independent passes, ideally on crossing tracks.
- Cameras off the runway axis, or several runways in use.
- A prior region (as in the original video), where the method acts as a check, not a search.

## Prop-traffic cam, 3 h, dopplertrack (2026-11-04)
Recommended follow-up to the airport tests: a camera with light-aircraft traffic, a long recording,
detected flyovers first, prop-only ADS-B, and exact timestamps.

- Audio: SWFL Eagle Cam (North Fort Myers, FL, known spot 26.7024, −81.8478), 3 h from
  2026-11-02 15:00 UTC, pulled from the live stream's ~5-day DASH rewind by `rewind_capture.py`.
  2160/2160 segments, none missing; each segment's `Ingestion-Walltime-Us` gives the wall clock.
  The media timeline is gap-free, but the stamps step ahead of it four times (2.1, 1.0, 3.7,
  1.8 s): the encoder dropped audio. Plain concatenation drifted 9.4 s by the end, so the WAV is
  rebuilt with silence at each step; audio time now matches the stamps to ±0.1 s (p5–p95).
- ADS-B: adsb.lol day archive via `dopplertrack fetch-day`, 130 × 130 km box: 1115 traces,
  147 after dropping known jets. (Needed a one-line fix to dopplertrack's asset sort.)
- Flyovers: `dopplertrack detect --band 60 300 --top 25`.

| Check | Result |
|---|---|
| Measured non-jet approaches heard at the nest in 3 h | **none within 2 km**; 1 within 4 km (P28A, 3.5 km); 16 within 12 km (repeat passes counted, tracks split at ADS-B gaps > 60 s) |
| Detections that line up with a real approach | 1 of 25 within ±5 s, 3 within ±30 s; slid-clock null p ≈ 0.4–0.5, i.e. chance |
| Helicopter (AS50) | a first pass at the gap-free audio looked like a 1.8 km match, but that CPA was interpolated across a 20 min ADS-B gap; its only measured approach (2.6 km) sits at the edge of another gap and is excluded |
| Strongest 5 detections | no ADS-B approach within 12 km: aircraft without ADS-B in the archive, or not aircraft |
| Known-point check (`search --point`), top-2 pair | 0 candidates: the fitted speeds/distances fall outside dopplertrack's ±35 % / 0.4–2.5× gates for every real flight |
| Blind pair search, Nov 2 traces | best score 7.99, **14.7 km** off; implies the clock is off by −1.1 h vs the segment stamps → rejected |
| Wrong-day null, Nov 1 traces | best score 7.92 (as high as the real day), 16.1 km off; implies −27.4 h → rejected |

Verdict: **no location from this window.** It failed at the input, not the search: in this 3 h
weekday-morning window no ADS-B-visible light aircraft was measured passing within 2 km of the
camera, and the loud flyovers that were detected are not in the ADS-B archive. The blind search
still produced a confident-looking answer (the wrong day's data scored as well), and the
timestamp check rejects it. Any blind result should be
checked against the recording's own clock. dopplertrack's eagle-cam success used 12 h of audio;
a fair retest needs a longer window (or several) and a camera under a busier pattern.

Reproduce:
    ../.venv/bin/python rewind_capture.py YGaF2La0A9o --from 2026-11-02T15:00:00Z --hours 3 --name swfl/swfl_1102
    # (or --rebuild --name swfl/swfl_1102 to redo the WAV/timeline from a saved .m4a)
    cd ../third_party/dopplertrack
    uv run --frozen dopplertrack spectrogram ../../echoloc/data/swfl/swfl_1102.wav ../../echoloc/data/swfl/swfl_1102_spec.npz
    uv run --frozen dopplertrack detect ../../echoloc/data/swfl/swfl_1102_spec.npz --band 60 300 --top 25 --out ../../echoloc/data/swfl/detected.json
    uv run --frozen dopplertrack fetch-day 2026-11-02 ../../echoloc/data/swfl/traces_2026-11-02.npz --bbox 26.12 27.29 -82.50 -81.20
    uv run --frozen python -I ../../echoloc/check_passes.py ../../echoloc/data/swfl/swfl_1102 ../../echoloc/data/swfl/traces_2026-11-02.npz --at 26.702359 -81.847766
    uv run --frozen dopplertrack search ../../echoloc/data/swfl/swfl_1102_spec.npz --events EV1 EV2 --traces ../../echoloc/data/swfl/traces_2026-11-02.npz --bbox 26.12 27.29 -82.50 -81.20 --out blind.csv

## Reproduce (airport webcams)
    ../.venv/bin/python live_capture.py --cams mdw,lhr,lis --minutes 25
    ../.venv/bin/python run_table.py lhr        # ~10–18 min per cam, 9 processes
    ../.venv/bin/python rescore.py lhr mdw lis [--topk 3]
