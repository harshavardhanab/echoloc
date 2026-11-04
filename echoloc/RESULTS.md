# echoloc — results (2026-11-03)

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

## Reproduce
    ../.venv/bin/python live_capture.py --cams mdw,lhr,lis --minutes 25
    ../.venv/bin/python run_table.py lhr        # ~10–18 min per cam, 9 processes
    ../.venv/bin/python rescore.py lhr mdw lis [--topk 3]
