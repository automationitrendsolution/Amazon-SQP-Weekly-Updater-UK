#!/usr/bin/env python3
"""Amazon SQP weekly updater (UK) -- Brand Analytics "Search Query Performance", command-line entry point.

Requests Amazon.co.uk's weekly SQP report (SP-API, report type
GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT) for the ASINs in
config/asins.txt, checks it, and writes it to powerbidb.brandanalytics_uk: rows of the
week that match (ASIN, search query) are updated, new ones inserted, nothing deleted, in one
transaction. ``--inspect`` and dry runs read the table only (read-only session).

Amazon can keep a report queued for hours. Each report ID is kept in data/state/, so a run
that is cut off or times out (exit 6) carries on with the same reports next time, and
batches already downloaded to data/reports/ are not requested again.

Running under cron
------------------
The scheduler (Windows Task Scheduler, deploy/install_task.ps1, or cron, deploy/crontab.example)
calls ``run.py --weekly --apply --quiet`` every Monday at 15:00 IST for the week that ended on
Saturday, with backup runs Tuesday-Friday for when Amazon keeps a report queued for a day or
more. The first run that completes writes the week; later runs find it written and stop at once.
Overlapping runs are refused by an internal lock (exit 11).

Exit codes
----------
    0   success
    1   unexpected error (see logs/sqp_updater.log)
    2   configuration error
    3   MySQL failed, or a write was rolled back (nothing changed)
    5   the period is not a valid or available SQP week (Sunday-Saturday)
    6   SP-API request failed, or the report was not ready in time (the next run resumes it)
    7   the report was for another week/marketplace, or had rejected or conflicting rows
    11  another run holds the lock
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # no __pycache__ folders in the project; set before app/ is imported

from app import __version__, powerbi, report, spapi
from app.asins import AsinList, load_asin_list
from app.config import Settings, load_settings
from app.exceptions import ConfigurationError, DatabaseError, PipelineError
from app.logging_config import append_run, configure_logging, get_logger
from app.schedule import RunLock, check_available, latest_completed_week, parse_date, today_in, validate_week
from app.schema import canonical_fields
from app.spapi import batch_asins

_HELP_EPILOG = """
SQP data, Amazon.co.uk (SP-API -> brandanalytics_uk):
  python run.py --inspect                                 read-only check of the table and its ASINs
  python run.py --period 2026-10-25 2026-10-31 --dry-run  fetch, validate and preview one week; never writes
  python run.py --period 2026-10-25 2026-10-31 --apply    write that week to MySQL
  python run.py --weekly --apply                          the latest completed, available week (cron)
  python run.py --weekly                                  the same, as a dry run
  python run.py --period 2026-10-25 2026-10-31 --dry-run --refetch   ask Amazon again (ignore downloads)

exit codes:
  0 success · 1 unexpected · 2 config · 3 MySQL · 5 bad or unavailable week
  6 SP-API failed · 7 malformed report · 11 locked
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amazon-sqp-weekly-updater-uk",
        description="Fetch Amazon.co.uk SQP weekly data and upsert it into MySQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_HELP_EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"amazon-sqp-weekly-updater-uk {__version__}")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--inspect", action="store_true", help="Inspect table and ASINs (read-only).")
    modes.add_argument("--period", nargs=2, metavar=("START", "END"), help="Sunday and Saturday, YYYY-MM-DD.")
    modes.add_argument("--weekly", action="store_true", help="Process the latest completed, available week.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="Fetch, validate and preview; never writes.")
    action.add_argument("--apply", action="store_true", help="Write the validated changes to MySQL.")
    parser.add_argument("--refetch", action="store_true",
                        help="Ask Amazon again: ignore downloaded reports and pending report IDs of the week.")
    parser.add_argument("--downloaded-only", action="store_true",
                        help="With --period: use only the reports already downloaded; ASINs whose report is still "
                             "pending are left out (run again later for them). Amazon is not called.")
    parser.add_argument("--no-insert", action="store_true",
                        help="With --period: change existing rows only; new search queries are listed, not inserted.")
    parser.add_argument("--env-file", type=Path, metavar="PATH", help="Alternative .env file.")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Override LOG_LEVEL.")
    parser.add_argument("--quiet", action="store_true", help="Console shows warnings and errors only (cron).")
    parser.add_argument("--no-lock", action="store_true", help="Skip the single-run lock (supervised runs only).")
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.inspect and (args.dry_run or args.apply):
        parser.error("--inspect cannot be combined with --dry-run/--apply")
    if args.period and not (args.dry_run or args.apply):
        parser.error("--period requires --dry-run or --apply")
    if (args.downloaded_only or args.no_insert) and not args.period:
        parser.error("--downloaded-only and --no-insert are for a manual --period run only")
    if args.downloaded_only and args.refetch:
        parser.error("--downloaded-only cannot be combined with --refetch")
    if args.weekly and not (args.dry_run or args.apply):
        # Scheduled runs must say --apply explicitly; anything else previews.
        args.dry_run = True


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)  # the downloaded reports stay private to this user
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    started = dt.datetime.now()
    try:
        settings = load_settings(args.env_file)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return exc.exit_code
    settings.ensure_directories()
    level = args.log_level or settings.log_level
    configure_logging(settings.log_dir, level=level, console_level="WARNING" if args.quiet else level)
    logger = get_logger("cli")

    code, summary = 1, ""
    try:
        code, summary = _run(args, settings, logger)
    except PipelineError as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        code, summary = exc.exit_code, f"{type(exc).__name__}: {exc}"
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        code = 130
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected failure")
        code, summary = 1, f"{type(exc).__name__}: {exc}"
    append_run(settings.log_dir, f"{started:%Y-%m-%d %H:%M:%S} exit={code} {' '.join(argv or sys.argv[1:])} | {summary}")
    return code


def _run(args: argparse.Namespace, settings: Settings, logger) -> tuple[int, str]:
    if args.inspect:
        code, lines = powerbi.inspection(settings, load_asin_list(settings.asin_file))
        print("\n".join(lines))
        return code, "inspected" if code == 0 else "no ASIN column"

    lock = RunLock(settings.log_dir / ".run.lock") if not args.no_lock else None
    if lock:
        lock.__enter__()
    try:
        return _update_week(args, settings, logger)
    finally:
        if lock:
            lock.__exit__(None, None, None)


def _week(args: argparse.Namespace, settings: Settings, logger) -> tuple[dt.date, dt.date]:
    """--period as given (a Sunday-Saturday week), or with --weekly the latest available one."""
    today = today_in(settings.timezone)
    if args.weekly:
        start, end = latest_completed_week(today, settings.availability_lag_days)
    else:
        start, end = parse_date(args.period[0]), parse_date(args.period[1])
        validate_week(start, end)
    warning = check_available(end, today, settings.availability_lag_days)
    if warning:
        logger.warning(warning)
    return start, end


def _asin_set(args: argparse.Namespace, settings: Settings, logger) -> AsinList:
    """The ASINs to request; --apply needs the unique count to equal EXPECTED_ASIN_COUNT."""
    asin_list = load_asin_list(settings.asin_file)
    for line in asin_list.summary(settings.expected_asin_count):
        logger.info("%s", line)
    problems = []
    if len(asin_list.asins) != settings.expected_asin_count:
        problems.append(f"{len(asin_list.asins)} unique ASINs in {asin_list.path.name}, "
                        f"EXPECTED_ASIN_COUNT is {settings.expected_asin_count}")
    if asin_list.invalid:
        problems.append(f"{len(asin_list.invalid)} unreadable line(s) in {asin_list.path.name}")
    for problem in problems:
        if args.apply:
            raise ConfigurationError(f"{problem}; make the ASIN set explicit before writing. Nothing was fetched.")
        logger.warning("%s (a dry run continues; --apply would stop)", problem)
    return asin_list


def _update_week(args: argparse.Namespace, settings: Settings, logger) -> tuple[int, str]:
    start, end = _week(args, settings, logger)
    state_file = spapi.state_file(settings.state_dir, start, end)
    previous = spapi.load_state(state_file)
    written, manual = previous.get("written"), previous.get("manual")
    if args.weekly and args.apply and written and not args.refetch:
        logger.info("%s..%s was already written on %s; nothing to do", start, end, written["at"])
        return 0, f"{start}..{end} already written"
    if args.weekly and args.apply and manual:
        logger.warning("%s..%s was written by hand (%s on %s); scheduled runs leave it alone: use --period",
                       start, end, manual["how"], manual["at"])
        return 0, f"{start}..{end} written by hand, left alone"
    asin_list = _asin_set(args, settings, logger)
    logger.info("requesting %s..%s, marketplace %s, %d ASINs in %d report(s)", start, end, settings.marketplace_id,
                len(asin_list.asins), len(batch_asins(asin_list.asins)))
    client = spapi.SpApiClient(settings)
    results = client.fetch_sqp(asin_list.asins, start, end, save_dir=settings.report_dir, state_file=state_file,
                               refetch=args.refetch, downloaded_only=args.downloaded_only)
    week = report.combine(results, start, end, settings.marketplace_id)
    left_out = sorted(set(asin_list.asins) - set(week.requested))
    if left_out:
        logger.warning("left out (report not downloaded yet, run again later): %d ASINs: %s", len(left_out),
                       " ".join(left_out))

    for line in week.summary():
        logger.info("%s", line)
    for item, reason in week.parsed.rejected[:50]:
        logger.error("rejected: %s / %r: %s", item.get("asin"), (item.get("searchQueryData") or {}).get("searchQuery"),
                     reason)
    for warning in [*week.parsed.warnings[:50], week.parsed.missing_summary()]:
        if warning:
            logger.warning("%s", warning)
    records_file = _save_records(week, settings)
    print("\n".join(["", *week.summary(), f"records: {records_file}"]))

    summary = (f"{start}..{end} reports={','.join(week.report_ids)} rows={len(week.parsed.records)} "
               f"rejected={len(week.parsed.rejected)} missing={len(week.missing)}")
    if week.parsed.rejected:
        logger.error("%d row(s) rejected: the week is not written (see above)", len(week.parsed.rejected))
        return 7, summary + " not written"

    if args.dry_run:
        plan = powerbi.plan_week(settings, week)
        plan_file = _save_plan(plan, week, settings)
        notes = [f"--no-insert: the {len(plan.inserts)} insert(s) will be skipped"] if args.no_insert else []
        print("\n".join([*plan.summary(), *notes, f"plan: {plan_file}",
                         f"dry run: nothing written to {settings.db_name}.{settings.db_table}"]))
        for line in plan.summary():
            logger.info("%s", line)
        return 0, f"{summary} dry-run insert={len(plan.inserts)} update={len(plan.updates)} repair={len(plan.repairs)}"

    insert = not args.no_insert
    plan = powerbi.write_week(settings, week, insert=insert)
    check = powerbi.plan_week(settings, week)  # read back: nothing may be left to change
    left = len(check.updates) + len(check.repairs) + (len(check.inserts) if insert else 0)
    if left:
        raise DatabaseError(f"{start}..{end} was written, but reading it back still shows {left} change(s); "
                            f"see {_save_plan(check, week, settings)}")
    inserted = len(plan.inserts) if insert else 0
    if args.no_insert or args.downloaded_only:  # a partial week by hand: the weekly runs must not complete it
        state = spapi.load_state(state_file)
        how = " ".join(flag for flag, on in (("--no-insert", args.no_insert),
                                             ("--downloaded-only", args.downloaded_only)) if on)
        state["manual"] = {"at": f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", "how": how}
        spapi.save_state(state_file, state)
    if insert and not left_out:  # only a complete week counts as written for the weekly runs
        state = spapi.load_state(state_file)
        state["written"] = {"at": f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", "inserted": inserted,
                            "updated": len(plan.updates), "repaired": len(plan.repairs),
                            "rows": len(week.parsed.records), "reports": week.report_ids}
        spapi.save_state(state_file, state)
    notes = [f"--no-insert: {len(plan.inserts)} new search queries not inserted"] if not insert else []
    notes += [f"left out until their report is downloaded: {len(left_out)} ASINs"] if left_out else []
    print("\n".join([*plan.summary(), *notes, f"written to {settings.db_name}.{settings.db_table}; read back: "
                     f"{check.existing_rows} rows for {check.reporting_date:%Y-%m-%d}, nothing left to change"]))
    return 0, (f"{summary} inserted={inserted} updated={len(plan.updates)} repaired={len(plan.repairs)} "
               f"unchanged={plan.unchanged}" + (f" left_out={len(left_out)}" if left_out else ""))


def _save_records(week: report.WeekReport, settings: Settings) -> Path:
    """The validated rows of the week as CSV, for reviewing a dry run."""
    path = settings.report_dir / f"sqp_{week.start}_{week.end}_records.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=canonical_fields())
        writer.writeheader()
        writer.writerows(week.parsed.records)
    return path


def _save_plan(plan: powerbi.WeekPlan, week: report.WeekReport, settings: Settings) -> Path:
    """Every planned change, one line per value: action, ASIN, query, column, old, new."""
    path = settings.report_dir / f"sqp_{week.start}_{week.end}_plan.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["action", "asin", "search_query", "column", "old", "new"])
        for (asin, query), changes, before in plan.updates:
            for column, value in changes.items():
                writer.writerow(["update", asin, query, column, before[column], value])
        for row in plan.inserts:
            writer.writerow(["insert", row["asin"], row["search_query"], "", "", ""])
        for asin, query in plan.ambiguous:
            writer.writerow(["skip: several rows", asin, query, "", "", ""])
        for (asin, stored), _, values in plan.repairs:
            writer.writerow(["repair: broken row", asin, values["search_query"], "search_query", stored,
                             values["search_query"]])
        for (asin, stored), _, amazon in plan.text_differs:
            writer.writerow(["unchanged: matched by numbers", asin, amazon, "search_query", stored, "(kept)"])
    return path


if __name__ == "__main__":
    sys.exit(main())
