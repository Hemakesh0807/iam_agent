"""
Local bulk test runner for SAML App Onboarding.

Usage:
    python bulk_test_runner.py                     # uses test_apps.csv by default
    python bulk_test_runner.py my_apps.csv         # use a custom CSV file

What it does:
    1. Reads each row from the CSV
    2. Detects gallery vs non-gallery
    3. Runs the full SAML SSO onboarding flow for each app
    4. Prints a detailed per-app result table with duration
    5. Prints a summary at the end
    6. Saves results to bulk_results.csv

Requirements:
    - .env file filled with real credentials
    - Service principal with Application.ReadWrite.All and Directory.ReadWrite.All
    - Fill in REPLACE_WITH_* placeholders in test_apps.csv before running
"""

import asyncio
import csv
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Load .env before any project imports ──────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bulk_test_runner.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("bulk_test_runner")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_divider(title: str = "") -> None:
    width = 72
    print("\n" + "=" * width)
    if title:
        print(f"  {title}")
        print("=" * width)


def _print_results_table(results) -> None:
    """Print a formatted result table with per-app duration."""
    col_name   = 28
    col_type   = 12
    col_status = 10
    col_dur    = 9
    col_info   = 25

    header = (
        f"  {'App Name':<{col_name}}"
        f"{'Type':<{col_type}}"
        f"{'Status':<{col_status}}"
        f"{'Duration':<{col_dur}}"
        f"Info / Error"
    )
    print(header)
    print("  " + "-" * 70)

    for r in results:
        name     = r.display_name[:col_name - 1].ljust(col_name)
        app_type = r.app_type[:col_type - 1].ljust(col_type)
        status   = ("✓ done" if r.status == "completed" else "✗ failed").ljust(col_status)
        duration = f"{r.duration_seconds:.1f}s".ljust(col_dur)

        if r.status == "completed":
            info = f"appId ends ..{r.app_id[-6:]}" if r.app_id else ""
        else:
            # Truncate error for display
            info = (r.error or "")[:col_info]

        print(f"  {name}{app_type}{status}{duration}{info}")


def _save_results_csv(results, output_path: str) -> None:
    """Save full results to a CSV file."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "row", "display_name", "app_type", "status",
            "app_id", "object_id", "sp_id",
            "cert_thumbprint", "duration_seconds", "error",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "row":              r.row,
                "display_name":     r.display_name,
                "app_type":         r.app_type,
                "status":           r.status,
                "app_id":           r.app_id or "",
                "object_id":        r.object_id or "",
                "sp_id":            r.sp_id or "",
                "cert_thumbprint":  r.cert_thumbprint or "",
                "duration_seconds": r.duration_seconds,
                "error":            r.error or "",
            })


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "test_apps.csv"

    _print_divider("AGENTIC IAM — Bulk SAML App Onboarding")
    print(f"  Started   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  CSV file  : {csv_file}")

    # Check CSV exists
    if not Path(csv_file).exists():
        print(f"\n  ERROR: CSV file not found: '{csv_file}'")
        print(  "  Create the file or pass a different path as an argument.")
        sys.exit(1)

    # Check for unfilled placeholders
    content = Path(csv_file).read_text()
    if "REPLACE_WITH_" in content:
        print("\n  ERROR: CSV contains unfilled placeholders.")
        print(  "  Replace all REPLACE_WITH_* values before running.")
        sys.exit(1)

    from app_bot.triggers.csv_bulk_trigger import handle_csv_bulk_onboarding

    overall_start = time.monotonic()

    try:
        results = await handle_csv_bulk_onboarding(csv_file)
    except FileNotFoundError as exc:
        print(f"\n  ERROR: {exc}")
        sys.exit(1)

    overall_duration = round(time.monotonic() - overall_start, 2)

    # ── Results table ─────────────────────────────────────────────────────────
    _print_divider("Results")
    _print_results_table(results)

    # ── Summary ───────────────────────────────────────────────────────────────
    total  = len(results)
    passed = sum(1 for r in results if r.status == "completed")
    failed = sum(1 for r in results if r.status == "failed")

    _print_divider("Summary")
    print(f"  Total apps   : {total}")
    print(f"  Passed       : {passed} ✓")
    print(f"  Failed       : {failed} ✗")
    print(f"  Total time   : {overall_duration}s")

    if failed:
        print(f"\n  Failed apps:")
        for r in results:
            if r.status == "failed":
                print(f"    Row {r.row:>2} — {r.display_name}")
                print(f"           {r.error}")

    # ── Save results CSV ──────────────────────────────────────────────────────
    output_csv = "bulk_results.csv"
    _save_results_csv(results, output_csv)
    print(f"\n  Full results saved to : {output_csv}")
    print(f"  Full logs saved to    : bulk_test_runner.log")
    print("=" * 72 + "\n")

    # Exit with error code if any app failed
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())