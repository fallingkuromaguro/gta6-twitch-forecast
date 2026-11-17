"""Leave-one-out backtest of main.py on the comparables, scored with the contest's log-scale pinball loss.

Usage: python3 tools/backtest.py <input-dir>   (the kit's input/ holds the practice cases; any case's comparables work)

Each comparable launch in turn is the target: the entry sees its hour-1 print and the other launches only, forecasts
hours 2..24, and is scored on the target's own hourly counts for those hours (the same item loss as RULES.txt:
2 x max(tau x d, (tau - 1) x d), d = ln(y) - ln(q), y at least 1). The house persistence forecast is scored the same
way. Two groups are reported: every launch, and the launches whose hour 1 was already post-unlock (the entry's own
MIN_ANALOG_RATIO test, the kind of day the GTA VI case is).

This measures how well the shape generalises across launches. It is not the grader's number: the practice answers
are held by the server, and the final is graded on the frozen recording.
"""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main as entry  # noqa: E402  (after the sys.path line)

LEVELS = entry.LEVELS
Z = entry.Z


def pinball_items(y: float, q: list[float]) -> list[float]:
    items = []
    for tau, qq in zip(LEVELS, q):
        dd = math.log(max(1.0, y)) - math.log(qq)
        items.append(2 * max(tau * dd, (tau - 1) * dd))
    return items


def score(rows: list[dict], truth: dict[int, float]) -> float:
    items: list[float] = []
    for row in rows:
        if row["hour"] in truth:
            items.extend(pinball_items(truth[row["hour"]], row["q"]))
    return sum(items) / len(items)


def load_comparables(input_dir: Path) -> list[dict]:
    """The comparables of the first case in the directory (the practice cases share one set)."""
    case = json.loads(sorted(input_dir.glob("*.in"))[0].read_text(encoding="utf-8"))
    return case["comparables"]


def launch_hours(comparables: list[dict]) -> list[dict]:
    """Each comparable with a usable release day: its name and its hour -> viewers map for hours 1..24."""
    launches = []
    for comp in comparables:
        by_hour = {}
        day_start = entry.parse_utc(comp["day_start"])
        for point in comp.get("hourly") or []:
            if point.get("viewers") is None:
                continue
            offset = (entry.parse_utc(point["from"]) - day_start).total_seconds() / 3600
            if offset.is_integer() and 0 <= offset < 24:
                by_hour[int(offset) + 1] = max(1.0, float(point["viewers"]))
        if 1 in by_hour and len(by_hour) >= entry.MIN_LAUNCH_HOURS:
            launches.append({"name": comp["name"], "hours": by_hour})
    return launches


def main() -> None:
    input_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "input")
    comparables = load_comparables(input_dir)
    launches = launch_hours(comparables)
    groups = {"all launches": lambda L: True,
              "post-unlock hour 1": lambda L: L["hours"][1] >= entry.MIN_ANALOG_RATIO * max(L["hours"].values())}
    for label, keep in groups.items():
        model_scores, persist_scores = [], []
        for target in launches:
            if not keep(target):
                continue
            comps = [c for c in comparables if c["name"] != target["name"]]
            p1 = target["hours"][1]
            case = {"known": [{"hour": 1, "viewers": p1}],
                    "forecast": [{"hour": h} for h in range(2, 25)],
                    "comparables": comps}
            truth = {h: target["hours"][h] for h in range(2, 25) if h in target["hours"]}
            model_scores.append(score(entry.forecast_hours(case), truth))
            persist_scores.append(score(entry.persistence_hours(case), truth))
        if not model_scores:
            continue
        m = sum(model_scores) / len(model_scores)
        p = sum(persist_scores) / len(persist_scores)
        print(f"{label:20s} targets={len(model_scores):2d}  entry={m:.4f}  persistence={p:.4f}  ratio={m / p:.3f}")


if __name__ == "__main__":
    main()
