"""
Page 9 — Bulk User Onboarding
Upload a CSV, run pre-flight validation, optionally dry-run,
then onboard all users with per-row progress and a full report.
"""
import asyncio
import csv
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Bulk User Onboarding — Agentic IAM",
    page_icon="👥",
    layout="wide",
)
st.title("👥 Bulk User Onboarding")
st.caption("Upload a CSV to onboard multiple users. Pre-flight validation runs before any accounts are created.")

# ── CSV format reference ──────────────────────────────────────────────────────
with st.expander("📄 Required CSV format", expanded=False):
    st.markdown("""
| Column | Required | Description |
|---|---|---|
| `display_name` | ✅ | Full name shown in Entra ID |
| `user_principal_name` | ✅ | UPN e.g. jane@company.com |
| `department` | ✅ | Department name |
| `job_title` | ✅ | Job title |
| `usage_location` | ✅ | Country name or 2-letter code e.g. India / IN |
| `mail_nickname` | optional | Defaults to UPN prefix |
| `manager_upn` | optional | Manager's UPN — resolved to object ID |
| `group_names` | optional | Pipe-separated group display names e.g. DevOps\\|Engineering |
| `license_name` | optional | License display name e.g. Microsoft 365 E3 |

**Notes:**
- `usage_location` accepts full country names ("India", "United States") or ISO codes ("IN", "US")
- `group_names` uses `|` as separator — spaces around `|` are trimmed
- Leave optional fields blank, not absent — the column must exist in the header
    """)

# ── File upload ───────────────────────────────────────────────────────────────
uploaded = st.file_uploader("Upload CSV file", type=["csv"])

if not uploaded:
    st.info("Upload a CSV file to get started.", icon="📂")
    st.stop()

raw_content = uploaded.read().decode("utf-8")

from user_bot.triggers.csv_bulk_user_trigger import parse_csv, preflight_validate, resolve_and_prepare
from user_bot.flows.bulk_user_onboarding import run_bulk_user_onboarding
from shared.models import BulkUserRowStatus

rows = parse_csv(raw_content)

if not rows:
    st.error("CSV file is empty or has no data rows.")
    st.stop()

st.success(f"✅ CSV loaded — {len(rows)} user(s) found.")

# ── CSV preview ───────────────────────────────────────────────────────────────
st.subheader("Preview")
st.dataframe(rows, use_container_width=True, hide_index=True)

st.divider()

# ── Pre-flight validation ─────────────────────────────────────────────────────
st.subheader("Pre-flight validation")

run_preflight = st.button("🔍 Run validation", type="secondary")

if "preflight_done" not in st.session_state:
    st.session_state["preflight_done"]    = False
    st.session_state["preflight_issues"]  = []
    st.session_state["preflight_missing"] = []
    st.session_state["preflight_rows"]    = 0

if run_preflight:
    with st.spinner("Validating CSV against Entra ID..."):
        issues, missing_cols = asyncio.run(preflight_validate(rows))
        st.session_state["preflight_done"]    = True
        st.session_state["preflight_issues"]  = issues
        st.session_state["preflight_missing"] = missing_cols
        st.session_state["preflight_rows"]    = len(rows)
        # Clear any previous run results when re-validating
        for k in ["run_summary", "prepared_rows"]:
            st.session_state.pop(k, None)

if st.session_state["preflight_done"]:
    issues      = st.session_state["preflight_issues"]
    missing_cols = st.session_state["preflight_missing"]

    # Missing columns — fatal
    if missing_cols:
        st.error(
            f"❌ Required columns missing from CSV: **{', '.join(missing_cols)}**\n\n"
            "Please add these columns and re-upload."
        )
        st.stop()

    errors   = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Total rows",  st.session_state["preflight_rows"])
    col2.metric("Errors",      len(errors),   delta=None)
    col3.metric("Warnings",    len(warnings), delta=None)

    if issues:
        # Show issues table
        issue_table = [
            {
                "Row":      i.row,
                "UPN":      i.upn,
                "Severity": "🔴 Error" if i.severity == "error" else "🟡 Warning",
                "Message":  i.message,
            }
            for i in issues
        ]
        st.dataframe(issue_table, use_container_width=True, hide_index=True)

    if errors:
        st.error(
            f"❌ {len(errors)} error(s) found. Fix them before running. "
            "Rows with errors will be skipped during the run."
        )
    elif warnings:
        st.warning(
            f"⚠ {len(warnings)} warning(s) — these rows will run but some sub-operations "
            "(groups/licenses) may be skipped."
        )
    else:
        st.success("✅ All rows passed validation. Ready to run.")

    st.divider()

    # ── Run options ───────────────────────────────────────────────────────────
    st.subheader("Run options")
    dry_run = st.checkbox(
        "🧪 Dry run — validate and simulate without creating any accounts",
        value=False,
        help="Runs through all rows and shows what would happen without making any API calls.",
    )

    # Only show run button if no blocking errors (or dry run)
    can_run = dry_run or len(errors) == 0
    skippable_upns = {i.upn for i in errors}

    if not can_run:
        st.warning(
            f"Fix the {len(errors)} error(s) above before running. "
            "Or enable Dry run to simulate.",
            icon="⚠️",
        )

    col_run, col_info = st.columns([1, 3])
    run_clicked = col_run.button(
        "🚀 Run bulk onboarding",
        type="primary",
        disabled=not can_run,
    )

    runnable_count = len(rows) - len({i.row for i in errors if not dry_run})
    col_info.caption(
        f"Will process **{runnable_count}** user(s). "
        + (f"{len(skippable_upns)} row(s) with errors will be skipped. " if errors and not dry_run else "")
        + ("**DRY RUN — no accounts will be created.**" if dry_run else "")
    )

    # Clear previous run results every time the button is clicked
    # so switching between dry run and real run always starts fresh
    if run_clicked:
        for k in ["run_summary", "prepared_rows"]:
            st.session_state.pop(k, None)

    # ── Run ───────────────────────────────────────────────────────────────────
    if run_clicked and "run_summary" not in st.session_state:
        st.divider()
        st.subheader("Processing" + (" (Dry Run)" if dry_run else ""))

        # Filter out error rows (skip them unless dry run)
        error_row_numbers = {i.row for i in errors}
        rows_to_run = [
            (i + 1, row) for i, row in enumerate(rows)
            if dry_run or (i + 1) not in error_row_numbers
        ]

        with st.spinner("Resolving names to IDs..."):
            prepared = asyncio.run(resolve_and_prepare([r for _, r in rows_to_run]))

        progress_bar  = st.progress(0, text="Starting...")
        log_container = st.empty()
        log_lines: list[str] = []

        def _log(line: str) -> None:
            log_lines.append(line)
            log_container.code("\n".join(log_lines[-25:]), language=None)

        # Run row by row with live progress
        from user_bot.flows.bulk_user_onboarding import run_bulk_user_onboarding
        from shared.models import BulkUserRunSummary, BulkUserResult, BulkUserRowStatus, BulkUserSubOp
        import asyncio as _asyncio

        # We need to run row by row for live progress — wrap the flow to yield results
        all_results = []
        overall_start = time.monotonic()

        for idx, (orig_row_num, _) in enumerate(rows_to_run):
            display_name = prepared[idx].get("display_name", f"Row {orig_row_num}")
            upn          = prepared[idx].get("user_principal_name", "")
            progress_bar.progress(
                idx / len(rows_to_run),
                text=f"Processing {idx + 1} of {len(rows_to_run)}: **{display_name}**",
            )
            _log(f"[{idx + 1}/{len(rows_to_run)}] {display_name} ({upn}) ...")

            row_result = asyncio.run(
                run_bulk_user_onboarding([prepared[idx]], dry_run=dry_run)
            )
            result = row_result.results[0]
            # Preserve original row number
            result.row = orig_row_num
            all_results.append(result)

            status_icon = {
                BulkUserRowStatus.COMPLETED: "✓",
                BulkUserRowStatus.PARTIAL:   "⚠",
                BulkUserRowStatus.FAILED:    "✗",
                BulkUserRowStatus.SKIPPED:   "—",
            }.get(result.status, "?")

            summary_line = result.summary_line()
            _log(f"  {status_icon} {display_name} — {result.duration_seconds:.1f}s"
                 + (f" — {summary_line}" if summary_line else ""))

        # Add skipped rows for errors
        if not dry_run:
            for i, row in enumerate(rows, start=1):
                if i in error_row_numbers:
                    upn = row.get("user_principal_name", f"row-{i}")
                    skipped_issues = [iss for iss in errors if iss.row == i]
                    all_results.append(BulkUserResult(
                        row=i,
                        display_name=row.get("display_name", upn),
                        upn=upn,
                        status=BulkUserRowStatus.SKIPPED,
                        error=skipped_issues[0].message if skipped_issues else "Pre-flight error",
                    ))

        # Sort by original row number
        all_results.sort(key=lambda r: r.row)

        progress_bar.progress(1.0, text="Done.")
        total_duration = round(time.monotonic() - overall_start, 2)

        from shared.models import BulkUserRunSummary
        summary = BulkUserRunSummary(
            total=len(all_results),
            completed=sum(1 for r in all_results if r.status == BulkUserRowStatus.COMPLETED),
            partial=sum(1 for r in all_results if r.status == BulkUserRowStatus.PARTIAL),
            failed=sum(1 for r in all_results if r.status == BulkUserRowStatus.FAILED),
            skipped=sum(1 for r in all_results if r.status == BulkUserRowStatus.SKIPPED),
            total_duration=total_duration,
            results=all_results,
            dry_run=dry_run,
        )
        st.session_state["run_summary"] = summary

    # ── Results ───────────────────────────────────────────────────────────────
    if "run_summary" in st.session_state:
        summary = st.session_state["run_summary"]
        st.divider()
        st.subheader("Results" + (" — Dry Run" if summary.dry_run else ""))

        # Summary banner
        if summary.failed == 0 and summary.partial == 0 and summary.skipped == 0:
            st.success(
                f"✅ All {summary.completed} user(s) onboarded successfully "
                f"in {summary.total_duration:.1f}s."
                + (" (Dry Run)" if summary.dry_run else "")
            )
        else:
            st.warning(
                f"Completed with issues — "
                f"✅ {summary.completed} completed, "
                f"⚠ {summary.partial} partial, "
                f"❌ {summary.failed} failed, "
                f"— {summary.skipped} skipped — "
                f"{summary.total_duration:.1f}s total."
            )

        # Summary metrics
        mc1, mc2, mc3, mc4, mc5 = st.columns(5)
        mc1.metric("Total",     summary.total)
        mc2.metric("✅ Done",   summary.completed)
        mc3.metric("⚠ Partial", summary.partial)
        mc4.metric("❌ Failed",  summary.failed)
        mc5.metric("— Skipped", summary.skipped)

        st.divider()

        # Results table
        table_data = []
        for r in summary.results:
            status_label = {
                BulkUserRowStatus.COMPLETED: "✅ completed",
                BulkUserRowStatus.PARTIAL:   "⚠ partial",
                BulkUserRowStatus.FAILED:    "❌ failed",
                BulkUserRowStatus.SKIPPED:   "— skipped",
            }.get(r.status, r.status.value)

            table_data.append({
                "Row":          r.row,
                "Display Name": r.display_name,
                "UPN":          r.upn,
                "Status":       status_label,
                "Duration (s)": r.duration_seconds,
                "User ID":      r.user_id or "",
                "Details":      r.summary_line(),
            })
        st.dataframe(table_data, use_container_width=True, hide_index=True)

        # Sub-operation details for partial/failed rows
        problem_rows = [r for r in summary.results if r.status in (
            BulkUserRowStatus.PARTIAL, BulkUserRowStatus.FAILED
        )]
        if problem_rows:
            st.subheader("Sub-operation details")
            for r in problem_rows:
                icon = "⚠" if r.status == BulkUserRowStatus.PARTIAL else "❌"
                with st.expander(f"{icon} Row {r.row} — {r.display_name} ({r.upn})"):
                    if r.user_id:
                        st.caption(f"User created — Object ID: `{r.user_id}` — some sub-operations failed.")
                    else:
                        st.caption("User was NOT created.")
                    for op in r.sub_ops:
                        op_icon = "✅" if op.success else "❌"
                        st.write(f"{op_icon} **{op.name}**: {op.detail}")

        # Download results CSV
        st.divider()
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            "row", "display_name", "upn", "status",
            "user_id", "duration_seconds", "details",
        ])
        writer.writeheader()
        for r in summary.results:
            writer.writerow({
                "row":              r.row,
                "display_name":     r.display_name,
                "upn":              r.upn,
                "status":           r.status.value,
                "user_id":          r.user_id or "",
                "duration_seconds": r.duration_seconds,
                "details":          r.summary_line(),
            })

        st.download_button(
            label="⬇️ Download results CSV",
            data=output.getvalue(),
            file_name="bulk_user_results.csv",
            mime="text/csv",
        )

        # Reset for another run
        if st.button("🔄 Run again with a new CSV"):
            for k in ["preflight_done", "preflight_issues", "preflight_missing",
                      "preflight_rows", "run_summary", "prepared_rows"]:
                st.session_state.pop(k, None)
            st.rerun()

