"""
Local bulk test runner for Bulk User Onboarding.

Usage:
    python bulk_user_test_runner.py                      # uses test_users.csv
    python bulk_user_test_runner.py my_users.csv         # custom CSV
    python bulk_user_test_runner.py my_users.csv --dry   # dry run

What it does:
    1. Parses the CSV
    2. Runs pre-flight validation
    3. If validation passes, resolves all names to IDs
    4. Runs the bulk onboarding flow row by row
    5. Prints a full report with per-row sub-op results
    6. Saves results to bulk_user_results.csv
"""

import asyncio
import csv
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bulk_user_test_runner.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("bulk_user_test_runner")


def _divider(title: str = "") -> None:
    w = 72
    print("\n" + "=" * w)
    if title:
        print(f"  {title}")
        print("=" * w)


async def main() -> None:
    csv_file = "test_users.csv"
    dry_run  = False

    for arg in sys.argv[1:]:
        if arg == "--dry":
            dry_run = True
        elif not arg.startswith("--"):
            csv_file = arg

    _divider("AGENTIC IAM — Bulk User Onboarding Test Runner")
    print(f"  Started  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  CSV file : {csv_file}")
    print(f"  Dry run  : {dry_run}")

    if not Path(csv_file).exists():
        print(f"\n  ERROR: File not found: '{csv_file}'")
        sys.exit(1)

    content = Path(csv_file).read_text(encoding="utf-8")

    if "YOURDOMAIN" in content or "REPLACE_WITH" in content:
        print("\n  ERROR: CSV contains unfilled placeholders.")
        print("  Replace YOURDOMAIN and REPLACE_WITH_* values before running.")
        sys.exit(1)

    from user_bot.triggers.csv_bulk_user_trigger import (
        parse_csv, preflight_validate, resolve_and_prepare
    )
    from user_bot.flows.bulk_user_onboarding import run_bulk_user_onboarding
    from shared.models import BulkUserRowStatus

    # Parse
    rows = parse_csv(content)
    print(f"\n  Rows found: {len(rows)}")

    # Pre-flight
    _divider("Pre-flight Validation")
    issues, missing_cols = await preflight_validate(rows)

    if missing_cols:
        print(f"  FATAL: Missing columns: {missing_cols}")
        sys.exit(1)

    errors   = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    for issue in issues:
        icon = "ERROR  " if issue.severity == "error" else "WARNING"
        print(f"  [{icon}] Row {issue.row:>2} {issue.upn}: {issue.message}")

    if not issues:
        print("  All rows passed validation.")

    if errors and not dry_run:
        print(f"\n  {len(errors)} error(s) found — these rows will be skipped.")

    # Resolve
    _divider("Resolving Names to IDs")
    error_rows  = {i.row for i in errors}
    rows_to_run = [r for i, r in enumerate(rows, 1) if dry_run or i not in error_rows]
    prepared    = await resolve_and_prepare(rows_to_run)
    print(f"  Resolved {len(prepared)} row(s).")

    # Run
    _divider("Running" + (" (Dry Run)" if dry_run else ""))
    overall_start = time.monotonic()
    summary = await run_bulk_user_onboarding(prepared, dry_run=dry_run)
    total_time = round(time.monotonic() - overall_start, 2)

    # Report
    _divider("Results")
    col_w = 28
    print(f"  {'Name':<{col_w}} {'UPN':<40} {'Status':<12} {'Time':>6}  Details")
    print("  " + "-" * 100)

    for r in summary.results:
        status_label = {
            BulkUserRowStatus.COMPLETED: "✓ done",
            BulkUserRowStatus.PARTIAL:   "⚠ partial",
            BulkUserRowStatus.FAILED:    "✗ failed",
            BulkUserRowStatus.SKIPPED:   "— skipped",
        }.get(r.status, r.status.value)

        name = r.display_name[:col_w - 1].ljust(col_w)
        upn  = r.upn[:38].ljust(40)
        dur  = f"{r.duration_seconds:.1f}s".rjust(6)
        det  = (r.summary_line() or "")[:50]
        print(f"  {name} {upn} {status_label:<12} {dur}  {det}")

        # Sub-op detail for partial/failed
        if r.status in (BulkUserRowStatus.PARTIAL, BulkUserRowStatus.FAILED):
            for op in r.sub_ops:
                icon = "  ✓" if op.success else "  ✗"
                print(f"       {icon} {op.name}: {op.detail}")

    # Summary
    _divider("Summary")
    print(f"  Total     : {summary.total}")
    print(f"  Completed : {summary.completed}")
    print(f"  Partial   : {summary.partial}")
    print(f"  Failed    : {summary.failed}")
    print(f"  Skipped   : {summary.skipped}")
    print(f"  Total time: {total_time}s")
    if dry_run:
        print("  (Dry run — no accounts were created)")

    # Save results
    out_file = "bulk_user_results.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "row", "display_name", "upn", "status",
            "user_id", "duration_seconds", "details",
        ])
        writer.writeheader()
        for r in summary.results:
            writer.writerow({
                "row": r.row, "display_name": r.display_name,
                "upn": r.upn, "status": r.status.value,
                "user_id": r.user_id or "", "duration_seconds": r.duration_seconds,
                "details": r.summary_line(),
            })

    print(f"\n  Results saved to : {out_file}")
    print(f"  Logs saved to    : bulk_user_test_runner.log")
    print("=" * 72 + "\n")

    sys.exit(0 if (summary.failed == 0 and summary.skipped == 0) else 1)


if __name__ == "__main__":
    asyncio.run(main())

