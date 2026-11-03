# Amazon SQP Weekly Updater (UK)

Every week, this job downloads the **Brand Analytics › Search Query Performance (SQP)** report for the
EcoNour UK store (Amazon.co.uk) and writes it to the Power BI database. It runs on its own from cron, so the
Power BI SQP data stays up to date with no manual work.

| | |
| --- | --- |
| **Data** | Per ASIN and search query: the weekly funnel of impressions, clicks, basket adds and purchases |
| **Source** | Amazon SP-API report `GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT`, Amazon.co.uk (`A1F83G8C2ARO7P`) |
| **Destination** | MySQL `powerbidb.brandanalytics_uk` |
| **Products** | 139 EcoNour ASINs, listed in [`config/asins.txt`](config/asins.txt) |
| **Schedule** | Every Monday at 15:00 IST for the previous week, with backup runs Tuesday to Friday |
| **Safety** | Only adds or corrects rows. It **never deletes**, never touches other weeks and never creates duplicates |

This is the same data you see in Seller Central › Brand Analytics › Search Query Performance with the country
set to *United Kingdom*, one week at a time.

A second, separate job collects **competitor data**: for each of our ASINs and search queries, the top 10
competing products from Seller Central's *Search Query Details* page. That page isn't available through the
SP-API, so this job reads it in Chrome with a saved Seller Central sign-in. See
[Competitor data](#competitor-data-seller-central).

| Job | Source | MySQL table | Schedule |
| --- | --- | --- | --- |
| SQP (weekly funnel) | SP-API | `brandanalytics_uk` | Monday 15:00 IST (+ Tue–Fri backups) |
| Competitors (top 10 per query) | Seller Central UK pages, in Chrome | `BrandAnalytics_Competitor_UK` | Daily, 10:00 / 13:00 / 16:00 UK time |

---

## Contents

1. [How it works](#how-it-works)
2. [Setting up the cron server](#setting-up-the-cron-server)
3. [Configuration](#configuration)
4. [Scheduling](#scheduling)
5. [Running it by hand](#running-it-by-hand)
6. [Amazon's limits and queue](#amazons-limits-and-queue)
7. [How data is written](#how-data-is-written)
8. [Competitor data (Seller Central)](#competitor-data-seller-central)
9. [Monitoring and troubleshooting](#monitoring-and-troubleshooting)
10. [Running on a Windows PC instead](#running-on-a-windows-pc-instead)
11. [Project structure](#project-structure)
12. [Technical reference](#technical-reference)

---

## How it works

```
 config/asins.txt (139 ASINs)
        │
        ▼
 1. Request    8 reports from Amazon (Amazon allows about 18 ASINs per report)
        │
        ▼
 2. Wait       Amazon builds the reports in its queue (usually minutes, sometimes hours)
        │
        ▼
 3. Check      right week, marketplace and report type; prices in GBP; every row valid
        │
        ▼
 4. Compare    with the rows already in brandanalytics_uk: insert / update / unchanged
        │
        ▼
 5. Write      in one transaction, then read back to confirm nothing is left to change
```

**Reporting weeks** run **Sunday to Saturday**. A week becomes available on the **Monday** after it ends
(UK time). For example, week 44 of 2026 is 25–31 October, which Seller Central shows as
`weekly-week=2026-10-31`.

**It picks up where it left off.** Each report ID is saved as soon as Amazon accepts it. If a run is stopped
or times out, the next run continues with the same reports instead of asking Amazon again.

**Bad data is never written.** A failed, incomplete or empty report is never stored as an empty week. If a
single row fails the checks, nothing for that week is written.

---

## Setting up the cron server

### Requirements

| | |
| --- | --- |
| **Python** | 3.13, managed with [uv](https://docs.astral.sh/uv/) |
| **Database** | MySQL `powerbidb`; the user needs `SELECT`, `INSERT` and `UPDATE` (no `DELETE`) |
| **Amazon SP-API** | A Login with Amazon app with the **Brand Analytics** role, authorised for the UK (Europe) seller account |
| **Competitor data only** | A Seller Central UK login with Brand Analytics access; Google Chrome (or Playwright's Chromium); it runs hidden, so no screen is needed |

### Step by step

**1. Get the code**

```bash
git clone <repository-url> /data/cron/amazon-sqp-weekly-updater-uk
cd /data/cron/amazon-sqp-weekly-updater-uk
```

**2. Add the settings file**

`.env` holds passwords and tokens, so it is **not** in git. Copy it from a machine that already has it, or
create it from the template:

```bash
cp .env.example .env      # then fill in the values (see Configuration)
chmod 600 .env            # readable by this user only
```

To copy it from a Windows PC instead, run this on the PC:
`scp .env root@<server>:/data/cron/amazon-sqp-weekly-updater-uk/.env`

**3. Install the packages**

```bash
uv sync
```

**4. Check everything is connected**

```bash
uv run python run.py --inspect                              # can it reach MySQL? is the ASIN list right?
uv run python run.py --period 2026-10-25 2026-10-31 --dry-run   # full trial run for one week; writes nothing
```

**5. Add the cron line.** See [Scheduling](#scheduling).

---

## Configuration

All settings live in `.env`, created from [`.env.example`](.env.example). Never commit `.env`. Any value that
still contains `replace-me` counts as not set.

### Amazon SP-API

| Setting | What it is |
| --- | --- |
| `SPAPI_LWA_CLIENT_ID` | The app's Login with Amazon client ID (`amzn1.application-oa2-client...`) |
| `SPAPI_LWA_CLIENT_SECRET` | The app's client secret (`amzn1.oa2-cs.v1...`) |
| `SPAPI_LWA_REFRESH_TOKEN` | Refresh token from authorising the app for the UK seller account (`Atzr\|...`) |
| `SPAPI_MARKETPLACE_ID` | `A1F83G8C2ARO7P` (Amazon.co.uk). The Europe endpoint is chosen from it |
| `SPAPI_ENDPOINT` | Optional. Overrides the regional endpoint |

The client ID and secret are in Seller Central › Apps & Services › Develop Apps › your app › *View* LWA
credentials. AWS keys and role ARNs are **not** needed.

### Database

| Setting | What it is |
| --- | --- |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` | MySQL server and login. The `POWERBI_DB_*` names work too |
| `DB_NAME` | `powerbidb` |
| `DB_TABLE` | `brandanalytics_uk`. The US table `2024BrandAnalytics` is refused |

### Behaviour (the defaults are fine)

| Setting | Default | What it does |
| --- | --- | --- |
| `EXPECTED_ASIN_COUNT` | `139` | Must match the number of unique ASINs in `config/asins.txt`, otherwise writing is refused |
| `SQP_TIMEZONE` | `Europe/London` | Time zone used to decide which week is the latest finished one |
| `SQP_AVAILABILITY_LAG_DAYS` | `2` | Days after Saturday before a week counts as available (2 = Monday) |
| `SQP_CURRENCY` | `GBP` | The only currency accepted; a report in another currency is refused |
| `SQP_POLL_INTERVAL_SECONDS` | `300` | How often to check on Amazon (5 minutes) |
| `SQP_POLL_TIMEOUT_SECONDS` | `79200` | How long one run waits for Amazon (22 hours); the next run continues after that |
| `SQP_MAX_RETRIES` | `6` | Quick retries for a busy or unreachable Amazon before waiting longer |
| `SQP_DATA_DIR`, `SQP_LOG_DIR` | `data/`, `logs/` | Optional other folders |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` for more detail |

### Seller Central (competitor data only)

| Setting | Default | What it does |
| --- | --- | --- |
| `SELLERCENTRAL_EMAIL`, `SELLERCENTRAL_PASSWORD` | – | The Seller Central login (`AMAZON_EMAIL` / `AMAZON_PASSWORD` work too) |
| `SELLERCENTRAL_TOTP_SECRET` | – | Optional, only with your team lead's OK: the authenticator-app key, so codes are generated by the job |
| `SC_COMPETITORS_FROM` | `2026-10-25` | First week (its Sunday) the scraping covers; earlier weeks are left alone |
| `SC_BROWSER_CHANNEL` | `chrome` | `chrome` (Google Chrome installed) or `chromium` (Playwright's own browser) |
| `SC_HEADLESS` | `true` | Chrome runs hidden. `false` shows the window, only to watch a problem |

### The ASIN list

[`config/asins.txt`](config/asins.txt) has one product per line: the ASIN, then a short name. Lines starting
with `#` are ignored.

- **To add or remove a product**, edit the file, then set `EXPECTED_ASIN_COUNT` in `.env` to the new number of
  unique ASINs. This check stops an accidentally emptied or half-edited file from being used.
- The list was built on 2026-11-03 from `business_reports_Uk` for week 44: every child ASIN whose title starts
  with *EcoNour*. Eight more are listed but commented out (seven non-EcoNour supplements and one with no
  title). SQP only covers your own brand, and an ASIN Amazon won't accept can make a whole report fail.
- The names are for reference only; the table has no name column.

---

## Scheduling

Run the job in **one place only**: the cron server **or** a Windows PC, never both.

The job runs **Monday at 15:00 IST** for the week that ended on Saturday. Runs from **Tuesday to Friday** at the
same time are backups. They only do something if Amazon was slow and the week isn't written yet. Once the week
is written, a backup run sees that and stops at once.

### Cron (server clock on New York time)

India doesn't change its clocks but New York does, so 15:00 IST is a different New York hour in summer and
winter. First check which Linux the server runs: `cat /etc/os-release`.

**Amazon Linux, RHEL, CentOS or Rocky:** use `CRON_TZ` and the job stays at 15:00 IST all year.

```cron
MAILTO=""
CRON_TZ=Asia/Kolkata
0 15 * * 1-5 cd /data/cron/amazon-sqp-weekly-updater-uk && /root/.local/bin/uv run --no-sync python run.py --weekly --apply --quiet >> logs/cron-output.log 2>&1
```

**Ubuntu or Debian:** their cron ignores `CRON_TZ`, so write the time in New York time.

```cron
MAILTO=""
# 15:00 IST = 04:30 New York in winter (EST). From mid-March to early November (EDT) change 30 4 to 30 5.
30 4 * * 1-5 cd /data/cron/amazon-sqp-weekly-updater-uk && /root/.local/bin/uv run --no-sync python run.py --weekly --apply --quiet >> logs/cron-output.log 2>&1
```

Add the line with `crontab -e`. Cron has a minimal `PATH`, so use the full path to `uv` (find it with
`which uv`). A copy of these lines is in [`deploy/crontab.example`](deploy/crontab.example).

| Cron time | Meaning |
| --- | --- |
| `0 15 * * 1-5` | 15:00, Monday to Friday |
| `0 15 * * 1` | 15:00, Monday only (no backup runs) |

### Cron for the competitor data

Three parts a day during UK business hours, each opening up to 400 pages. This needs the one-time
Seller Central sign-in first (see [Competitor data](#competitor-data-seller-central)).

```cron
CRON_TZ=Europe/London
0 10,13,16 * * * cd /data/cron/amazon-sqp-weekly-updater-uk && flock -n logs/.sc-cron.lock timeout 3h15m /root/.local/bin/uv run --no-sync python run.py --weekly-scrape --quiet >> logs/cron-output.log 2>&1
```

On Ubuntu or Debian (no `CRON_TZ`), use New York time: `0 5,8,11 * * *`. Chrome runs hidden, so no screen
is needed. `flock` stops two parts running at once, and `timeout` ends a part that
runs too long.

---

## Running it by hand

On the server, start every command with `uv run python run.py`. On Windows, use
`.venv\Scripts\python.exe -B run.py`.

| I want to… | Command |
| --- | --- |
| Check the database connection and ASIN list (read-only) | `run.py --inspect` |
| Try a week without writing anything | `run.py --period 2026-10-25 2026-10-31 --dry-run` |
| Write a specific week (for example, to fill a gap) | `run.py --period 2026-10-25 2026-10-31 --apply` |
| Do exactly what the schedule does | `run.py --weekly --apply` |
| Ask Amazon again instead of reusing downloaded reports | add `--refetch` |
| See all options | `run.py --help` |
| Run the automated tests | `uv run python -B -m pytest` |

`--period` always takes a **Sunday** and the following **Saturday**.

**Review a dry run before writing.** A dry run saves two files in `data/reports/`:

- `sqp_<start>_<end>_records.csv`: every row Amazon sent, after the checks
- `sqp_<start>_<end>_plan.csv`: every insert and every changed value (old → new)

A following `--apply` for the same week reuses the downloaded reports, so Amazon isn't asked twice.

<details>
<summary>Advanced options</summary>

| Option | Use |
| --- | --- |
| `--downloaded-only` (with `--period`) | Write only the reports already downloaded; the rest are left for later |
| `--no-insert` (with `--period`) | Update existing rows only; new search queries are listed, not inserted |
| `--quiet` | Show only warnings and errors on screen (used by cron) |
| `--no-lock` | Skip the "one run at a time" lock (only when you are watching) |
| `--env-file PATH` | Use another settings file |

</details>

---

## Amazon's limits and queue

Two things can make a run take a while. Both are normal and handled automatically.

**1. Amazon limits how fast new reports can be requested.** It accepts a few at once, then roughly one
every two minutes. When the limit is reached Amazon answers **HTTP 429 / `QuotaExceeded`**. The job then waits
(5 minutes at a time) and tries again, for as long as the run lasts. In the log it looks like this:

```
WARNING POST .../reports -> HTTP 429 (request id ...); retry 1 in 2s
WARNING Amazon's quota for new reports is used up; trying again in 300s
```

This doesn't block or harm the account. The limit is shared by every program that uses the same SP-API app
and seller account, so other jobs can use it up.

**2. Amazon builds reports in a queue.** Each report goes through `IN_QUEUE` → `IN_PROGRESS` → `DONE`. That
usually takes about a minute, but on busy days it can take hours. The job checks every 5 minutes and waits up
to 22 hours. If that runs out, the next day's backup run continues with the same reports.

> **Tip:** don't stop a run because of 429 warnings. If you do, nothing is lost: running the same command
> again continues with the reports Amazon has already accepted.

---

## How data is written

`brandanalytics_uk` has no key column, so rows are matched by **reporting date + ASIN + search query**. The
search query is compared ignoring upper/lower case and extra spaces.

| Situation | What happens |
| --- | --- |
| The row exists and Amazon's numbers differ | Only the changed columns are updated |
| The row exists and the numbers are the same | Left unchanged |
| A new search query | Inserted |
| A row is in the table but not in Amazon's report | **Kept** (never deleted) |
| Amazon leaves a value empty | The table's existing value is kept |
| The same key is found on several rows | Skipped and listed; the program never guesses |

- **One transaction.** Any error rolls back the whole week, and the table stays exactly as it was. After
  writing, the week is read back and must show nothing left to change.
- **`reporting_date`** is the week's Saturday: week 44 of 2026 is stored as `2026-10-31`.
- **Percentages** are stored with 2 decimals (`0.68` means 0.68 %), **prices** in GBP with 2 decimals.
  `basket_adds_asin_share_percent` and `purchases_asin_share_percent` are whole-number columns, so those two
  are rounded (16.67 % → `17`), matching the rows already in the table.
- The `*_shipping_percent` columns hold **counts** (same-day / 1-day / 2-day), the same as Seller Central's
  download.
- **Missing ASINs:** an ASIN with no search activity in a week has no rows. The run summary lists them as
  "missing". This is normal and is not an error.
- The table's first week (2026-02-21, loaded by hand) has 107 search queries stored twice. They are left
  as they are; if that week is ever run again, those queries are skipped.

---

## Competitor data (Seller Central)

For every ASIN and search query of a week, Seller Central UK's **Search Query Details** page shows the top 10
products shoppers saw and clicked. The job opens that page for each pair, saves it, and stores the
competitors in `BrandAnalytics_Competitor_UK`:

```
https://sellercentral.amazon.co.uk/brand-analytics/dashboard/query-detail?view-id=query-detail-asin-view
    &asin=<ASIN>&search-term-freeform=<keyword>&reporting-range=weekly&weekly-week=<Saturday>&country-id=gb
```

### The commands

There are only two:

| I want to… | Command |
| --- | --- |
| Sign in once and save the session | `run.py --sc-login` |
| Test: scrape 20 pages and show what would be stored (nothing written to MySQL) | `run.py --weekly-scrape --dry-run --max-pages 20` |
| Scrape and store (what cron runs) | `run.py --weekly-scrape` |
| The same for one particular week | add `--week 2026-10-25 2026-10-31` |

Without `--week`, it works on the oldest week since `SC_COMPETITORS_FROM` that isn't finished, which is
normally the latest one.

### Does it need a Seller Central login?

| Step | Login needed? |
| --- | --- |
| SQP data (`--period … --dry-run`, `--weekly --apply`) | **No.** It uses the SP-API keys only |
| Competitor data (`--weekly-scrape`, also with `--dry-run`) | **Yes.** The pages are only visible when signed in |

You only sign in **once per machine**. Seller Central asks for a one-time code (OTP); tick
**"Don't require OTP on this browser"** and the job's own Chrome profile (`data/browser/`) remembers it. The
scraping never signs in by itself, so it never triggers an OTP. If the saved sign-in expires, the run stops and
says so; run `--sc-login` again.

This project has its own sign-in for `amazon.co.uk`. The US project's sign-in (`amazon.com`) is separate.

### The saved session

After signing in, the session is also saved to **`data/browser/sellercentral-session.json`**. Each scraping
run refreshes it. Chrome's own profile only works on the machine where you signed in, but this file can be
copied. To sign in the server without typing an OTP there, sign in on your PC and copy the file:

```powershell
scp data\browser\sellercentral-session.json root@<server>:/data/cron/amazon-sqp-weekly-updater-uk/data/browser/
```

The next run on the server loads it and starts signed in. A copied-in file is used once; after that, the
server keeps its own sign-in up to date.

> **Keep this file private.** It gives access to the Seller Central account, like a password. It's in `data/`,
> which git ignores, and it's created readable by your user only. If it leaks, sign out of all devices in
> Seller Central (Settings › Login settings) and run `--sc-login` again.

### First-time setup

Everything happens in the terminal; Chrome runs hidden (no window), on the PC and the server alike.

1. Add `SELLERCENTRAL_EMAIL` and `SELLERCENTRAL_PASSWORD` to `.env`.
2. Sign in once. It fills in your email and password, asks you to **type the OTP** in the terminal, opens a
   test page (Search Query Details for `B0D5YK4YDH` / "halloween decorations", week ending 2026-10-31) to
   prove the session works, and saves the session:

   | Where | Command |
   | --- | --- |
   | This PC | `.venv\Scripts\python.exe -B run.py --sc-login` |
   | The server | `uv run python run.py --sc-login` |
   | The server, without an OTP there | Sign in on the PC, then copy the [session file](#the-saved-session) |

3. Test with a few pages. Pages are saved, but nothing is written to MySQL:
   `run.py --weekly-scrape --dry-run --max-pages 20`
4. If the numbers look right, add the cron line ([Scheduling](#cron-for-the-competitor-data)), or run
   `run.py --weekly-scrape` yourself. The pages from step 3 are reused, not opened again.

### How it runs

- **Which keywords:** every search query of each ASIN in that week's SQP data, highest search volume first.
  Week 44 of 2026 has 2,090 pages (100 ASINs with search data).
- **In parts:** each scheduled part opens up to 400 pages (about 3 hours), pausing 4–9 seconds between pages
  like a person would. The next part continues where the last one stopped, so a week takes about two days.
- **Week by week:** a part always works on the oldest week (from `SC_COMPETITORS_FROM`) that isn't fully
  scraped. After each part, everything saved so far is stored in the table.
- **It stops by itself** on a captcha, a security check, an "Access Required" page, an expired sign-in or
  5 pages in a row without the table, so the account is never pushed further.

### How rows are stored

| Column | From the page |
| --- | --- |
| `reporting_date` | The week's Saturday |
| `asin`, `keyword` | Our ASIN and the search query |
| `title`, `competitor_asin`, `competitor_brand` | The competing product |
| `price_median` | Median price in GBP |
| `impressions`, `impression_share`, `clicks`, `click_share` | The competitor's numbers for that query (shares in %) |

Rows are only **inserted**, never updated or deleted. A row already stored for the same week, ASIN, keyword
and competitor is skipped, so running a week again adds nothing. Each page is saved as JSON in
`data/sellercentral/<Saturday>/` before anything is stored.

---

## Monitoring and troubleshooting

### Logs

| File | What's in it |
| --- | --- |
| `logs/cron.log` | **Start here.** One line per run: time, exit code and a summary (rows inserted, updated…) |
| `logs/sqp_updater.log` | Full detail: report IDs, statuses, warnings. Rotated daily, 30 days kept |
| `logs/cron-output.log` | Screen output from cron runs |

A healthy week looks like this in `logs/cron.log`:

```
2026-11-03 14:48:24 exit=0 --weekly --apply | 2026-10-25..2026-10-31 ... rows=2090 rejected=0 inserted=2090 updated=0
```

### Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| **0** | Success | Nothing |
| **1** | Unexpected error | Read `logs/sqp_updater.log` |
| **2** | A setting is missing or wrong | Read the message and fix `.env` |
| **3** | Database problem: MySQL unreachable, or the write was rolled back (**nothing changed**) | Check the `DB_*` settings and the message |
| **4** | Seller Central sign-in needed, or its page isn't as expected | Run `--sc-login`; the screenshot in `data/screenshots/` shows the page |
| **5** | Not a Sunday–Saturday week, or the week isn't finished | Fix the `--period` dates; `--weekly` picks the week itself |
| **6** | Amazon request failed, or the report wasn't ready in time | A timeout continues on the next run. HTTP 403: the app needs the Brand Analytics role |
| **7** | The report was for the wrong week or marketplace, not in GBP, or had invalid rows | Nothing was written; the raw report is in `data/reports/` |
| **11** | Another run was still going | Nothing; the next run continues |

### Common questions

| Symptom | Cause and fix |
| --- | --- |
| Lots of `HTTP 429` warnings | Amazon's request limit. Normal: the job waits and continues. See [Amazon's limits and queue](#amazons-limits-and-queue) |
| A report ends with `FATAL` | Often an ASIN not covered by your brand registration. Comment it out in `config/asins.txt`, lower `EXPECTED_ASIN_COUNT`, run again |
| The week still isn't written on Friday | Amazon was very slow. The next Monday moves on to the new week, so write the missed one by hand with `--period … --apply` |
| New data isn't in Power BI | The data is in MySQL; refresh the Power BI dataset |
| I want to redo a week from scratch | Run it with `--refetch`, or delete `data/state/sqp_<start>_<end>.json` |
| The credentials were exposed or changed | Create a new client secret in Seller Central and update `.env` |
| Scraping: "the saved Seller Central sign-in has expired" | Run `--sc-login` once; the next part continues |
| Scraping stopped with "asks for a check or sign-in" or "captcha" | Amazon wants a person. Open Seller Central UK in a browser, then run `--sc-login` |
| Scraping: "5 pages in a row without the table" | Seller Central was slow or changed its page. Look at the screenshot in `data/screenshots/`, or set `SC_HEADLESS=false` and run `--weekly-scrape --dry-run --max-pages 3` to watch it |

---

## Running on a Windows PC instead

Use this only if the job is **not** on the cron server.

```powershell
cd C:\Users\Admin\Desktop\amazon-sqp-weekly-updater-uk
uv sync
copy .env.example .env                                    # then fill in the values
.venv\Scripts\python.exe -B run.py --inspect
powershell -ExecutionPolicy Bypass -File deploy\install_task.ps1           # schedule: Monday 15:00 + Tue–Fri backups
powershell -ExecutionPolicy Bypass -File deploy\install_task.ps1 -Remove   # remove the schedule
```

For the competitor data: `deploy\install_competitors_task.ps1` (daily 15:30, 18:30, 21:30 IST; task
**Amazon SQP Competitors UK**), after `run.py --sc-login`.

The task is called **Amazon SQP Weekly Updater UK**. The PC must be on with the user signed in (a locked
screen is fine). If the PC was off at the scheduled time, the run starts as soon as it is back on.

---

## Project structure

| Path | Purpose |
| --- | --- |
| `run.py` | Command-line entry point (`--help` lists every option) |
| `app/spapi.py` | Amazon SP-API: sign-in, requesting, waiting for and downloading reports, retries, resuming |
| `app/report.py` | Checks the report and combines the 8 reports into one week |
| `app/powerbi.py` | MySQL: inspection, the change plan, the transactional write |
| `app/schema.py` | Report fields and the table columns they go to |
| `app/schedule.py` | Sunday–Saturday weeks, the latest available week, the one-run-at-a-time lock |
| `app/asins.py` | Reads `config/asins.txt` |
| `app/sellercentral.py` | Seller Central UK in Chrome: the one-time sign-in, page inspection |
| `app/competitors.py` | Search Query Details pages: scraping, reading, storing in `BrandAnalytics_Competitor_UK` |
| `app/config.py`, `app/exceptions.py`, `app/logging_config.py` | Settings, exit codes, logging |
| `config/asins.txt` | The 139 UK ASINs with short names |
| `deploy/` | Cron lines and the Windows Task Scheduler script |
| `data/` (not in git) | Downloaded reports, run state, saved Seller Central pages and the browser sign-in |
| `logs/` (not in git) | Run history and logs |
| `tests/` | Automated tests (no Amazon account or real database needed) |

### Differences from the US updater

This project is the UK copy of `amazon-sqp-weekly-updater` (US). The code and checks are the same.

| | US | UK |
| --- | --- | --- |
| Marketplace / endpoint | `ATVPDKIKX0DER` / `sellingpartnerapi-na` | `A1F83G8C2ARO7P` / `sellingpartnerapi-eu` |
| Table | `2024BrandAnalytics` | `brandanalytics_uk` (own column names; no `id`, `week`, `year`, `name`) |
| ASINs | 60, in 4 reports | 139, in 8 reports |
| Currency | USD | GBP |
| Week decided in | US Pacific time | UK time |
| Competitor scraping | `sellercentral.amazon.com` → `BrandAnalytics_Competitor` | `sellercentral.amazon.co.uk` → `BrandAnalytics_Competitor_UK` (own columns, no name/index) |

---

## Technical reference

<details>
<summary>Report fields → <code>brandanalytics_uk</code> columns</summary>

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

A row is rejected, and the week not written, if it has an ASIN that wasn't requested, no search query, a
negative or fractional count, or no `searchQueryVolume` / `asinImpressionCount`. Exact duplicate rows are
dropped; conflicting duplicates make the whole report refused.

</details>

<details>
<summary>SP-API calls</summary>

1. `POST https://api.amazon.com/auth/o2/token` with the refresh token → access token (renewed before it
   expires).
2. `POST https://sellingpartnerapi-eu.amazon.com/reports/2021-06-30/reports` with `marketplaceIds:
   [A1F83G8C2ARO7P]`, `reportPeriod: WEEK` and up to 200 characters of ASINs (8 reports for 139 ASINs). All
   reports are requested before waiting. On `QuotaExceeded` the request is repeated every
   `SQP_POLL_INTERVAL_SECONDS` until the run's time limit.
3. `GET /reports/2021-06-30/reports/{id}` every `SQP_POLL_INTERVAL_SECONDS` until `DONE`. A `FATAL` report
   carries an error document, which is logged.
4. `GET /reports/2021-06-30/documents/{id}` → a pre-signed URL; the (gzip) JSON is saved to `data/reports/`.

HTTP 429, 5xx and network errors are retried with exponential backoff. Report IDs are kept in `data/state/`,
so an interrupted run continues with the same reports.

</details>
