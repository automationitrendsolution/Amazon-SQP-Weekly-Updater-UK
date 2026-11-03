# Amazon SQP Weekly Updater (UK)

Keeps the Power BI database up to date with Amazon **Brand Analytics: Search Query Performance** data for the
EcoNour UK store (Amazon.co.uk, 139 ASINs). It is the UK copy of `amazon-sqp-weekly-updater` (US): same code
and checks, writing to the existing UK table `brandanalytics_uk`. It runs on a schedule and needs no manual work
once it is set up.

| Data | Source | MySQL table (`powerbidb`) | Schedule |
| --- | --- | --- | --- |
| **Search Query Performance**: per ASIN and search query, the weekly funnel (impressions, clicks, basket adds, purchases) | Amazon **SP-API** report `GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT`, marketplace `A1F83G8C2ARO7P` | `brandanalytics_uk` | Weekly, Monday |

This is the same data as Seller Central → Brand Analytics → Search Query Performance with the country set to
United Kingdom (`country-id=gb`), one week at a time.

The updater only adds or corrects data. **It never deletes rows**, never touches other weeks, and never writes
duplicates.

### Differences from the US updater

| | US | UK |
| --- | --- | --- |
| Marketplace / SP-API endpoint | `ATVPDKIKX0DER` / `sellingpartnerapi-na` | `A1F83G8C2ARO7P` / `sellingpartnerapi-eu` |
| Table | `2024BrandAnalytics` | `brandanalytics_uk` (its own column names; no `id`, `week`, `year`, `name`) |
| ASINs | 60, 4 reports a week | 139, 8 reports a week |
| Prices | USD only | GBP only |
| Week decided in | US Pacific time | UK time (`Europe/London`) |
| Schedule | Monday 15:00 IST | Monday 15:30 IST |
| Competitor scraping (Seller Central) | Yes | Not included |

Writing to the US table is refused (`DB_TABLE=2024BrandAnalytics` stops the run with exit 2).

---

## How it works

```
config/asins.txt (139 ASINs)
      │
      ▼
Amazon SP-API ── 8 reports per week (Amazon allows ~18 ASINs per report), requested together
      │          Amazon queues them; a run waits up to 22 h and later runs resume the same reports
      ▼
Validate ─────── same week, marketplace and report type; prices in GBP; bad rows rejected; batches combined
      │
      ▼
Plan ─────────── compare with the week's rows already in brandanalytics_uk: insert / update / unchanged
      │
      ▼
Write ────────── one transaction, then read back: nothing may be left to change
```

- Weeks run **Sunday to Saturday**. A week counts as available **two days after it ends** (Monday, UK time).
  Week 44 of 2026 is 25–31 October (`weekly-week=2026-10-31` in Seller Central).
- A failed, incomplete or empty report is **never** written as an empty week.

---

## Requirements

| | |
| --- | --- |
| Python | 3.13, managed with [uv](https://docs.astral.sh/uv/) |
| Database | MySQL `powerbidb` with the user allowed to `SELECT`, `INSERT`, `UPDATE` (no `DELETE` needed) |
| Amazon SP-API | A Login with Amazon app with the **Brand Analytics** role, authorised for the UK (Europe) seller account |

---

## Installation

### Windows (this PC)

```powershell
cd C:\Users\Admin\Desktop\amazon-sqp-weekly-updater-uk
uv sync                                              # creates .venv with Python 3.13 and the packages
copy .env.example .env                               # then fill in the values (see Configuration)
.venv\Scripts\python.exe -B run.py --inspect         # read-only check of the table and the ASIN list
.venv\Scripts\python.exe -B run.py --period 2026-10-25 2026-10-31 --dry-run   # week 44, preview only
.venv\Scripts\python.exe -B run.py --period 2026-10-25 2026-10-31 --apply     # week 44, written
```

### Linux server

```bash
git clone <repository-url> /data/cron/amazon-sqp-weekly-updater-uk
cd /data/cron/amazon-sqp-weekly-updater-uk
cp .env.example .env && chmod 600 .env    # then fill in the values
uv sync
uv run python run.py --inspect
```

On Windows, run commands as `.venv\Scripts\python.exe -B run.py ...`; on Linux as `uv run python run.py ...`.

---

## Configuration

All settings live in `.env` (copied from `.env.example`). `.env` holds passwords and tokens: keep it private
and **never commit it**. Values still containing `replace-me` count as not set.

### Amazon SP-API

| Setting | Description |
| --- | --- |
| `SPAPI_LWA_CLIENT_ID`, `SPAPI_LWA_CLIENT_SECRET` | Login with Amazon credentials of the SP-API app |
| `SPAPI_LWA_REFRESH_TOKEN` | Refresh token from authorising the app for the UK seller account (`Atzr|...`) |
| `SPAPI_MARKETPLACE_ID` | `A1F83G8C2ARO7P` (Amazon.co.uk), the default. The EU endpoint follows from it |
| `SPAPI_ENDPOINT` | Optional override of the regional endpoint |

AWS keys and role ARNs are **not** needed: SP-API accepts LWA-only requests.

### Database

| Setting | Description |
| --- | --- |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` | MySQL server and login (`POWERBI_DB_*` names work too) |
| `DB_NAME`, `DB_TABLE` | `powerbidb`, `brandanalytics_uk` |

### Behaviour

| Setting | Default | Description |
| --- | --- | --- |
| `EXPECTED_ASIN_COUNT` | `139` | Must equal the unique ASINs in `config/asins.txt`, otherwise writing is refused |
| `SQP_TIMEZONE` | `Europe/London` | Decides which week is the latest completed one |
| `SQP_AVAILABILITY_LAG_DAYS` | `2` | Days after Saturday before a week counts as available |
| `SQP_CURRENCY` | `GBP` | The only currency the table's prices may be in; a report in another one is refused |
| `SQP_POLL_INTERVAL_SECONDS` | `300` | How often a run checks Amazon's report status |
| `SQP_POLL_TIMEOUT_SECONDS` | `79200` | How long one run waits for Amazon (22 h); the next run resumes |
| `SQP_MAX_RETRIES` | `6` | Retries for throttled, failing or unreachable SP-API calls |
| `SQP_DATA_DIR`, `SQP_LOG_DIR` | `data/`, `logs/` | Optional other locations |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail |

### The ASIN list

[`config/asins.txt`](config/asins.txt): one `ASIN  short name` per line. It was built on 2026-11-03 from
`business_reports_Uk` for week 44: every child ASIN whose title starts with EcoNour (139). Names come from
`product_categorisation_uk` (Child Short Name), else the title.

- Eight ASINs are listed but commented out: seven supplement listings that are not EcoNour, and one with no
  title. SQP only covers your own brand's ASINs, and an ASIN Amazon does not allow can fail a whole report.
- To add or remove an ASIN, edit the file and set `EXPECTED_ASIN_COUNT` to the new number of unique ASINs.
- Names are for reference only: `brandanalytics_uk` has no name column.

---

## Usage

| Task | Command |
| --- | --- |
| Inspect the table and its ASINs (read-only) | `run.py --inspect` |
| Preview a week: fetch, validate, show what would change (writes nothing) | `run.py --period 2026-10-25 2026-10-31 --dry-run` |
| Write a week (backfill) | `run.py --period 2026-10-25 2026-10-31 --apply` |
| Latest available week, written (what the schedule runs) | `run.py --weekly --apply` |
| Ask Amazon again instead of reusing downloaded reports | add `--refetch` |
| Write only the batches already downloaded (rest later) | add `--downloaded-only` (with `--period`) |
| Change existing rows only, insert nothing | add `--no-insert` (with `--period`) |
| All options | `run.py --help` |
| Run the tests | `uv run python -B -m pytest` |

A dry run saves `data/reports/sqp_<start>_<end>_records.csv` (the validated rows) and `..._plan.csv` (every
insert and every changed value, old → new) for review. The `--apply` run reuses the dry run's downloads.

---

## Scheduling

Run the job in **one place only** (the server **or** a Windows PC), never both.

### Linux cron

Check the server's time zone with `timedatectl`, then add to `crontab -e` (see
[`deploy/crontab.example`](deploy/crontab.example)):

```cron
MAILTO=""
# Server clock on IST: Monday 15:30 IST; Tue–Fri are backups that stop at once when the week is written
30 15 * * 1-5 cd /data/cron/amazon-sqp-weekly-updater-uk && /root/.local/bin/uv run --no-sync python run.py --weekly --apply --quiet >> logs/cron-output.log 2>&1
# Server clock on UTC: 0 10 * * 1-5 ...same command...
```

### Windows Task Scheduler

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install_task.ps1           # Monday 15:30 (+ Tue–Fri backups)
powershell -ExecutionPolicy Bypass -File deploy\install_task.ps1 -Remove   # unregister
```

The task is called **Amazon SQP Weekly Updater UK**, separate from the US task. The PC must stay on with the
user signed in to Windows; a locked screen is fine.

---

## How data is written

`brandanalytics_uk` has no key column at all, so rows are matched by **(reporting_date, ASIN, search query)**,
the query compared without case and extra spaces.

| Situation | Result |
| --- | --- |
| Row exists, Amazon's numbers differ | Only the changed columns are updated |
| Row exists, numbers equal | Unchanged |
| New search query | Inserted with `reporting_date` and `asin` |
| Row in the table but not in Amazon's report | Kept |
| Amazon leaves a value empty | The table's value is kept |
| Key found on several rows | Skipped and listed, never guessed |

- `reporting_date` = the week's Saturday (a date): week 44 of 2026 is `2026-10-31`.
- A row is updated by its date, ASIN and search query **exactly as stored**, and only if that finds exactly one
  row; otherwise the whole week is rolled back.
- Everything happens in **one transaction**; any error rolls the whole week back. The week is read back
  afterwards and must show nothing left to change.
- Shares and rates are stored as percentages with 2 decimals (`0.68` = 0.68 %), prices in GBP with 2 decimals.
  Two columns are whole numbers in the table, `basket_adds_asin_share_percent` and
  `purchases_asin_share_percent`, so those shares are rounded (16.67 % → `17`), as in the rows already there.
  If they are ever changed to `FLOAT`, the updater keeps the decimals on its own.
- The `*_shipping_percent` columns hold counts (same-day / 1-day / 2-day), as Seller Central's download does.
- The table's first week (2026-02-21, loaded by hand) has 107 search queries stored twice. They are left as
  they are; that week is only touched if you run it again, and then the doubled queries are skipped.

---

## Monitoring and troubleshooting

| Log | Content |
| --- | --- |
| `logs/cron.log` | One line per run: time, exit code, command, summary (rows written, …) |
| `logs/sqp_updater.log` | Full details: report IDs and statuses, warnings (rotated daily, 30 days kept) |
| `logs/cron-output.log` | Console output of cron runs |

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | Success | – |
| 1 | Unexpected error | Read `logs/sqp_updater.log` |
| 2 | A setting is missing or wrong (also: the US table as `DB_TABLE`) | Read the message; fix `.env` |
| 3 | MySQL unreachable, table or column missing, or a write was rolled back (**nothing changed**) | Check the `DB_*` settings and the message |
| 5 | Not a Sunday–Saturday week, or the week is not complete | Fix `--period`; `--weekly` picks a valid week itself |
| 6 | SP-API request failed, or a report was not ready in time | Timeout: the next run resumes. HTTP 403: the app needs the Brand Analytics role for the UK account |
| 7 | Report for another week/marketplace, a price not in GBP, or rejected rows | Nothing written; the raw JSON is in `data/reports/` |
| 11 | Another run was still going | Nothing; the next run continues |

| Symptom | Cause and fix |
| --- | --- |
| A report ends `FATAL` | Often an ASIN not covered by your brand registration. Comment it out in `config/asins.txt`, lower `EXPECTED_ASIN_COUNT`, run again |
| Week takes days to arrive | Amazon processes SQP reports per account in one queue; the US and UK runs, or another program using the same app, share it. The updater resumes on its own |
| New weeks not visible in Power BI | The data is in MySQL; refresh the Power BI dataset |
| Start a week over | Delete `data/state/sqp_<start>_<end>.json` or use `--refetch` |

---

## Project structure

| Path | Purpose |
| --- | --- |
| `run.py` | Command-line entry point (`--help` lists every option) |
| `app/spapi.py` | SP-API: access token, reports, polling, download, retries, resume |
| `app/report.py` | Validates the report JSON, combines batches, finds missing ASINs |
| `app/powerbi.py` | MySQL: inspection, the week's plan, the transactional write |
| `app/schema.py` | Report fields and their table columns |
| `app/schedule.py` | Sunday–Saturday weeks, latest available week, run lock |
| `app/asins.py` | Reads `config/asins.txt` |
| `app/config.py`, `app/exceptions.py`, `app/logging_config.py` | Settings, exit codes, logging |
| `config/asins.txt` | The 139 UK ASINs with short names |
| `deploy/` | Cron line and Windows Task Scheduler setup |
| `data/` (not in git) | Downloaded reports and run state |
| `logs/` (not in git) | Run history and logs |
| `tests/` | Automated tests (no Amazon or real database needed) |

---

## Technical reference

<details>
<summary>SP-API report fields → <code>brandanalytics_uk</code> columns</summary>

| Report field | Column | Report field | Column |
| --- | --- | --- | --- |
| searchQuery | search_query | totalCartAddCount | basket_adds_total_count |
| searchQueryScore | search_query_score | totalCartAddRate | basket_add_rate_percent |
| searchQueryVolume | search_query_volume | asinCartAddCount | basket_adds_asin_count |
| totalQueryImpressionCount | impressions_total_count | asinCartAddShare | basket_adds_asin_share_percent (int) |
| asinImpressionCount | impressions_asin_count | totalMedianCartAddPrice | basket_adds_price_median |
| asinImpressionShare | impressions_asin_share_percent | asinMedianCartAddPrice | basket_adds_asin_price_median |
| totalClickCount | clicks_total_count | total{SameDay,OneDay,TwoDay}ShippingCartAddCount | basket_adds_{same_day,1d,2d}_shipping_percent |
| totalClickRate | clicks_click_rate_percent | totalPurchaseCount | purchases_total_count |
| asinClickCount | clicks_asin_count | totalPurchaseRate | purchases_purchase_rate_percent |
| asinClickShare | clicks_asin_share_percent | asinPurchaseCount | purchases_asin_count |
| totalMedianClickPrice | clicks_price_median | asinPurchaseShare | purchases_asin_share_percent (int) |
| asinMedianClickPrice | clicks_asin_price_median | totalMedianPurchasePrice | purchases_price_median |
| total{SameDay,OneDay,TwoDay}ShippingClickCount | clicks_{same_day,1d,2d}_shipping_percent | asinMedianPurchasePrice | purchases_asin_price_median |
| | | total{SameDay,OneDay,TwoDay}ShippingPurchaseCount | purchases_{same_day,1d,2d}_shipping_percent |

Rows with an unrequested ASIN, a missing search query, a negative or fractional count, or no
`searchQueryVolume` / `asinImpressionCount` are rejected and the week is not written. Exact duplicates are
dropped; conflicting duplicates refuse the report.

</details>

<details>
<summary>SP-API calls</summary>

1. `POST https://api.amazon.com/auth/o2/token` with the refresh token → access token (renewed before it expires).
2. `POST https://sellingpartnerapi-eu.amazon.com/reports/2021-06-30/reports` with `marketplaceIds:
   [A1F83G8C2ARO7P]`, `reportPeriod: WEEK` and up to 200 characters of ASINs (8 reports for 139 ASINs), all
   created before waiting.
3. `GET /reports/2021-06-30/reports/{id}` every `SQP_POLL_INTERVAL_SECONDS` until `DONE`. `FATAL` reports carry
   an error document, which is logged.
4. `GET /reports/2021-06-30/documents/{id}` → pre-signed URL; the (gzip) JSON is downloaded to `data/reports/`.

HTTP 429 and 5xx responses and network errors are retried with exponential backoff. Report IDs are kept in
`data/state/`, so an interrupted run resumes the same reports.

</details>
#   A m a z o n - S Q P - W e e k l y - U p d a t e r - U K  
 