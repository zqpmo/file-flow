# -*- coding: utf-8 -*-
"""File-Flow 命令行入口。默认 dry-run；须显式 --execute 才移动文件。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import engine

FF_ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="file-flow")
    p.add_argument("--ff-root", default=str(FF_ROOT))
    p.add_argument("--zones", default=str(FF_ROOT / "zones.json"))
    p.add_argument("--rules", default=str(FF_ROOT / "rules.json"))
    p.add_argument(
        "--execute",
        action="store_true",
        help="actually move files (default is dry-run)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="force dry-run (default; kept for compatibility)",
    )
    p.add_argument("--zone", action="append", dest="zones_filter", default=None)
    p.add_argument("--source", action="append", dest="extra_sources", default=[])
    p.add_argument("--rollback", dest="rollback_id", default=None)
    p.add_argument("--ai", action="store_true", help="run ai_worker after engine")
    args = p.parse_args(argv)
    # Default = dry-run. Only --execute (without conflicting --dry-run) moves files.
    dry_run = not args.execute or args.dry_run
    ff = Path(args.ff_root)
    if args.rollback_id:
        result = engine.rollback_run(ff, args.rollback_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    report = engine.run_once(
        ff,
        zones_path=Path(args.zones),
        rules_path=Path(args.rules),
        dry_run=dry_run,
        source_filter=args.zones_filter,
        extra_sources=args.extra_sources,
    )
    if args.ai and not dry_run and report.get("pending_ai", 0) > 0:
        import ai_worker

        ai_result = ai_worker.drain_pending(ff, limit=50)
        report["ai"] = ai_result
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
