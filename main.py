"""gta6-twitch-ccu-forecast-01 entry in Python 3.13 (standard library only): a launch-shape forecaster.

For each case (one <id>.in in the input directory) it forecasts hours 2..24 of the UTC release day from the case's
hour-1 print and the case's `comparables`, the past launches' release-day hourly curves:

  1. Shape. For every comparable that is a real launch (at least MIN_LAUNCH_HOURS of its 24 release-day hours on
     SullyGnome's hourly grid) and whose hour 1 was already post-unlock (hour 1 is at least MIN_ANALOG_RATIO of the
     launch's own release-day peak), take each hour's log ratio to hour 1: d_h = ln(V_h / V_1).
     A launch whose day began before its unlock (hour 1 near zero, peak hours later) cannot say how a launch that
     unlocked at midnight behaves, so it is left out of the shape.
  2. Quantiles. For hour h, pool the log ratios of hours h-1, h, h+1 across the kept launches (one sample per
     launch-hour) and take their empirical quantiles at 0.05, 0.25, 0.5, 0.75 and 0.95. Pooling steadies the
     quantiles for a few dozen launches.
  3. Forecast. q_tau(h) = p1 * exp(quantile_tau of d_h), where p1 is the case's hour-1 print (1 if below 1).
     The quantiles come from the data, so the spread widens with the launches' disagreement, hour by hour.

If a case cannot be read or modelled, the entry writes the house persistence forecast for it, so no case is left
without an output. The whole run is deterministic and needs no network.

How the grader runs it: `python3 -P -s /bot/main.py` (PYTHONHASHSEED=0) in python:3.13-slim: no network, no pip
packages. -P keeps this file's directory off sys.path, so the sys.path line below puts it back first (this entry
ships no other module).

Outside the grader's container, MAKEFUN_INPUT_DIR and MAKEFUN_OUTPUT_DIR stand in for /input and /output:
    mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The grader's -P leaves /bot off sys.path: put it back first, the same as the house starter does.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# The quantile levels the contest scores, in the order each row lists them.
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
# The standard normal's quantile at each level (the house persistence fallback).
Z = [-1.645, -0.674, 0, 0.674, 1.645]
# The house persistence spread, in natural-log units per square-root hour (the fallback only).
SIGMA = 0.15

FIRST_HOUR, LAST_HOUR = 2, 24
# A comparable counts as a launch only if at least this many of its 24 release-day hours are on the hourly grid.
MIN_LAUNCH_HOURS = 20
# Hour 1 must be at least this share of the launch's own release-day peak for the launch to shape the forecast.
# Leave-one-out scores are flat for cutoffs from 0.05 to 0.20 and best near 0.10 (tools/backtest.py).
MIN_ANALOG_RATIO = 0.10
# Each hour's quantiles pool the log ratios of the hours within this many hours of it.
POOL = 1
# Fewer pooled samples than this for an hour and the house persistence spread is used instead.
MIN_SAMPLE = 9


def parse_utc(text: str) -> datetime:
    """An ISO 8601 instant ('...Z' or an offset) as an aware datetime; a timestamp with no offset is read as UTC."""
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def round2(x: float) -> float | int:
    """x rounded to 2 decimals, half up, as JavaScript's Math.round(x * 100) / 100 rounds it.

    A whole number comes back as an int, so json.dumps writes 1240 where JavaScript writes 1240 (not 1240.0).
    """
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value.is_integer() else value


def quantile(sorted_values: list[float], p: float) -> float:
    """The p-quantile of a sorted list, linearly interpolated between its two nearest values."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    x = p * (len(sorted_values) - 1)
    i = math.floor(x)
    if i + 1 >= len(sorted_values):
        return sorted_values[-1]
    f = x - i
    return sorted_values[i] * (1 - f) + sorted_values[i + 1] * f


def launch_log_ratios(comparables: list[dict]) -> list[dict[int, float]]:
    """The kept launches' log ratios to hour 1: one dict per launch, hour -> ln(V_h / V_1), for hours 2..24."""
    shapes: list[dict[int, float]] = []
    for comp in comparables:
        if not comp.get("day_start") or not comp.get("hourly"):
            continue
        day_start = parse_utc(comp["day_start"])
        by_hour: dict[int, float] = {}
        for point in comp["hourly"]:
            if point.get("viewers") is None:
                continue
            offset = (parse_utc(point["from"]) - day_start).total_seconds() / 3600
            if offset.is_integer() and 0 <= offset < 24:
                by_hour[int(offset) + 1] = max(1.0, float(point["viewers"]))
        if 1 not in by_hour or len(by_hour) < MIN_LAUNCH_HOURS:
            continue
        if by_hour[1] < MIN_ANALOG_RATIO * max(by_hour.values()):
            continue
        logs = {h: math.log(v) for h, v in by_hour.items()}
        shapes.append({h: logs[h] - logs[1] for h in range(FIRST_HOUR, LAST_HOUR + 1) if h in logs})
    return shapes


def hour_log_quantiles(shapes: list[dict[int, float]], h: int) -> list[float] | None:
    """The five quantiles of ln(V_h / V_1) for hour h, pooled over the neighbouring hours, or None if too few."""
    sample: list[float] = []
    for j in range(max(FIRST_HOUR, h - POOL), min(LAST_HOUR, h + POOL) + 1):
        sample.extend(shape[j] for shape in shapes if j in shape)
    if len(sample) < MIN_SAMPLE:
        return None
    sample.sort()
    return [quantile(sample, p) for p in LEVELS]


def hour1_print(case: dict) -> float:
    """Hour 1's print (1 if below 1). Falls back to the last recorded whole-hour mean if the hour-1 row is missing."""
    for row in case.get("known") or []:
        if row.get("hour") == 1 and row.get("viewers") is not None:
            return max(1.0, float(row["viewers"]))
    for row in reversed(case.get("history_hourly") or []):
        if row.get("viewers") is not None:
            return max(1.0, float(row["viewers"]))
    return 1.0


def clamp_viewers(x: float) -> float:
    """A quantile kept inside the contract's 0 < q <= 1e9."""
    return min(1e9, max(0.01, x))


def forecast_hours(case: dict) -> list[dict]:
    """The 23 hourly rows of a case's forecast."""
    p1 = hour1_print(case)
    shapes = launch_log_ratios(case.get("comparables") or [])
    hours = [row["hour"] for row in case.get("forecast") or []] or list(range(FIRST_HOUR, LAST_HOUR + 1))
    rows = []
    for h in hours:
        logs = hour_log_quantiles(shapes, h)
        if logs is None:  # too few launches for this hour: the house persistence spread
            logs = [z * SIGMA * math.sqrt(h - 1) for z in Z]
        q: list[float | int] = []
        for x in logs:
            value = round2(clamp_viewers(p1 * math.exp(x)))
            if q and value < q[-1]:  # rounding can never break the order, but keep it explicit
                value = q[-1]
            q.append(value)
        rows.append({"hour": h, "q": q})
    return rows


def persistence_hours(case: dict) -> list[dict]:
    """The house persistence forecast, used only when a case cannot be modelled."""
    p1 = hour1_print(case)
    hours = [row["hour"] for row in case.get("forecast") or []] or list(range(FIRST_HOUR, LAST_HOUR + 1))
    return [{"hour": h, "q": [round2(clamp_viewers(p1 * math.exp(z * SIGMA * math.sqrt(h - 1)))) for z in Z]} for h in hours]


def forecast_case(case: dict, name: str) -> dict:
    try:
        if case.get("levels") is not None and list(case["levels"]) != LEVELS:
            print(f"{name}: levels {case['levels']} differ from {LEVELS}; writing the scored levels", file=sys.stderr)
        return {"hours": forecast_hours(case)}
    except Exception as exc:  # a broken case still gets an output: persistence keeps it valid
        print(f"{name}: falling back to persistence ({exc!r})", file=sys.stderr)
        return {"hours": persistence_hours(case)}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Every case file, in name order, so two runs write the same bytes.
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
