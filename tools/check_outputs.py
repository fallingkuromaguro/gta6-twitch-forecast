"""Check forecast files against the contest's output contract (RULES.txt, "Your forecast").

Usage: python3 tools/check_outputs.py <output-dir>

Each <id>.out must be a regular file of at most 65,536 bytes holding one JSON object with exactly the key "hours": 23
rows, hours 2..24 each once, each row exactly {"hour": h, "q": [q05, q25, q50, q75, q95]}, five finite numbers in
(0, 1e9], non-decreasing. Exits 1 if any file fails.
"""

import json
import math
import sys
from pathlib import Path

MAX_BYTES = 65536
LEVELS = 5


def check(path: Path) -> list[str]:
    problems = []
    if path.is_symlink() or not path.is_file():
        return ["not a regular file"]
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        problems.append(f"{len(raw)} bytes > {MAX_BYTES}")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return problems + [f"not JSON: {exc}"]
    if not isinstance(doc, dict) or set(doc) != {"hours"}:
        return problems + ["top level must be exactly {\"hours\": [...]}"]
    rows = doc["hours"]
    if not isinstance(rows, list) or len(rows) != 23:
        return problems + [f"hours must be a list of 23 rows, got {len(rows) if isinstance(rows, list) else type(rows).__name__}"]
    hours = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"hour", "q"}:
            problems.append(f"row keys must be exactly hour, q: {row!r:.80}")
            continue
        h, q = row["hour"], row["q"]
        hours.append(h)
        if not isinstance(q, list) or len(q) != LEVELS:
            problems.append(f"hour {h}: q must have {LEVELS} numbers")
            continue
        for x in q:
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not (0 < x <= 1e9):
                problems.append(f"hour {h}: quantile {x!r} not a finite number in (0, 1e9]")
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in q) and any(b < a for a, b in zip(q, q[1:])):
            problems.append(f"hour {h}: quantiles decrease: {q}")
    if sorted(hours) != list(range(2, 25)):
        problems.append("hours must be 2..24 each exactly once")
    return problems


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    files = sorted(out_dir.glob("*.out"))
    if not files:
        print(f"no .out files in {out_dir}")
        return 1
    failed = 0
    for path in files:
        problems = check(path)
        status = "PASS" if not problems else "FAIL"
        failed += bool(problems)
        print(f"{status} {path.name}" + ("" if not problems else ": " + "; ".join(problems)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
