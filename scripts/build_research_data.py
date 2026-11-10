"""Build the research dataset (thin wrapper around compounder.data.dataset).

Usage:  python scripts/build_research_data.py daily  --end 2026-10
        python scripts/build_research_data.py hourly --end 2026-10 --top 30

Equivalent to ``compounder download --end 2026-10 --top 30``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from compounder.data.dataset import build_daily, build_hourly  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["daily", "hourly"])
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--end", default="2026-10", help="last month to include (YYYY-MM)")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.stage == "daily":
        build_daily(args.data_dir, args.end, args.workers)
    else:
        build_hourly(args.data_dir, args.top, args.end, args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
