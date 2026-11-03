"""Shared fixtures: SQP report builders shaped like Amazon's JSON, isolated project folders."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

START, END = "2026-10-11", "2026-10-17"


def make_item(asin="B0TESTAAAA", query="Garlic Press", start=START, end=END, **overrides):
    """A dataByAsin entry shaped like Amazon's SQP report."""
    money = lambda amount: {"amount": amount, "currencyCode": "GBP"}
    item = {
        "startDate": start,
        "endDate": end,
        "asin": asin,
        "searchQueryData": {"searchQuery": query, "searchQueryScore": 1, "searchQueryVolume": 5000},
        "impressionData": {
            "totalQueryImpressionCount": 100000,
            "asinImpressionCount": 2500,
            "asinImpressionShare": 0.025,
        },
        "clickData": {
            "totalClickCount": 4000, "totalClickRate": 0.8, "asinClickCount": 120, "asinClickShare": 0.03,
            "totalMedianClickPrice": money(19.99), "asinMedianClickPrice": money(24.99),
            "totalSameDayShippingClickCount": 10, "totalOneDayShippingClickCount": 300,
            "totalTwoDayShippingClickCount": 2000,
        },
        "cartAddData": {
            "totalCartAddCount": 900, "totalCartAddRate": 0.18, "asinCartAddCount": 30, "asinCartAddShare": 0.033,
            "totalMedianCartAddPrice": money(18.99), "asinMedianCartAddPrice": money(24.99),
            "totalSameDayShippingCartAddCount": 2, "totalOneDayShippingCartAddCount": 80,
            "totalTwoDayShippingCartAddCount": 500,
        },
        "purchaseData": {
            "totalPurchaseCount": 400, "totalPurchaseRate": 0.08, "asinPurchaseCount": 12, "asinPurchaseShare": 0.03,
            "totalMedianPurchasePrice": money(17.99), "asinMedianPurchasePrice": money(24.99),
            "totalSameDayShippingPurchaseCount": 1, "totalOneDayShippingPurchaseCount": 40,
            "totalTwoDayShippingPurchaseCount": 200,
        },
    }
    for path, value in overrides.items():
        section, _, key = path.partition("__")
        if key:
            item[section][key] = value
        else:
            item[section] = value
    return item


def make_report(items, start=START, end=END, marketplace="A1F83G8C2ARO7P"):
    return {
        "reportSpecification": {
            "reportType": "GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT",
            "reportOptions": {"reportPeriod": "WEEK", "asin": " ".join({i["asin"] for i in items})},
            "dataStartTime": start,
            "dataEndTime": end,
            "marketplaceIds": [marketplace],
        },
        "dataByAsin": items,
    }


@pytest.fixture
def report_factory():
    return make_report


@pytest.fixture
def item_factory():
    return make_item


class FakeTable:
    """powerbidb.brandanalytics_uk in memory, answering the statements app/powerbi.py sends.

    Like the real table it has no id: rows are found by (reporting_date, asin, search_query), text compared
    without case as MySQL's collation does. Two share columns are INT, as in the real table. Read-only
    connections refuse writes like MySQL does; ``fail_on`` makes a statement starting with that text
    raise, to test the rollback.
    """

    INT_SHARES = {"basket_adds_asin_share_percent", "purchases_asin_share_percent"}

    def __init__(self):
        from app import schema

        self.rows: list[dict] = []
        self.read_only = True
        self.fail_on: str | None = None
        self.committed = 0
        self._snapshot = None
        decimals = {schema.TABLE_COLUMNS[f] for f in schema.PERCENT_FIELDS | schema.PRICE_FIELDS} - self.INT_SHARES
        self.types = {"search_query": "text", "reporting_date": "date", "asin": "text",
                      **{c: "float" if c in decimals else "int" for c in schema.METRIC_COLUMNS}}

    def add(self, **row):
        self.rows.append(row)
        return row

    def matches(self, row, day, asin, query):
        return (row["reporting_date"] == day and row["asin"].casefold() == asin.casefold()
                and row["search_query"].casefold() == query.casefold())

    # connection
    def cursor(self):
        return FakeTableCursor(self)

    def begin(self):
        self._snapshot = [dict(r) for r in self.rows]

    def commit(self):
        self._snapshot = None
        self.committed += 1

    def rollback(self):
        if self._snapshot is not None:
            # Inserts are appended, so the first rows are the ones from before; tests hold them, so their
            # values are restored in place.
            kept = self.rows[:len(self._snapshot)]
            for row, before in zip(kept, self._snapshot):
                row.clear()
                row.update(before)
            self.rows = kept
        self._snapshot = None


class FakeTableCursor:
    def __init__(self, table: FakeTable):
        self.table, self.result = table, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def _check(self, sql):
        import pymysql

        if self.table.fail_on and sql.startswith(self.table.fail_on):
            raise pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")
        if self.table.read_only and sql.startswith(("UPDATE", "INSERT", "DELETE")):
            raise pymysql.err.OperationalError(1792, "Cannot execute statement in a READ ONLY transaction.")

    def execute(self, sql, params=()):
        import re

        sql = " ".join(sql.split())
        self._check(sql)
        rows = self.table.rows
        if sql.startswith("SELECT COLUMN_NAME, DATA_TYPE"):
            self.result = [{"COLUMN_NAME": c, "DATA_TYPE": t} for c, t in self.table.types.items()]
        elif sql.startswith("SELECT asin, search_query"):
            self.result = [dict(r) for r in rows if r["reporting_date"] == params[0]]
        elif sql.startswith("SELECT COUNT(*)") and len(params) == 3:
            self.result = [{"n": sum(1 for r in rows if self.table.matches(r, *params))}]
        elif sql.startswith("SELECT COUNT(*)"):
            self.result = [{"n": sum(1 for r in rows if r["reporting_date"] == params[0])}]
        elif sql.startswith("UPDATE"):
            columns = re.findall(r"`(\w+)` = %s", sql.split(" WHERE ")[0])
            for row in rows:
                if self.table.matches(row, *params[-3:]):
                    row.update(zip(columns, params[:-3]))
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def executemany(self, sql, seq):
        import re

        sql = " ".join(sql.split())
        self._check(sql)
        assert sql.startswith("INSERT"), sql
        columns = re.findall(r"`(\w+)`", sql.split(" VALUES ")[0])[1:]  # [0] is the table
        for values in seq:
            self.table.rows.append(dict(zip(columns, values)))

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0]


@pytest.fixture
def fake_table(monkeypatch):
    """Replace the MySQL connection with a FakeTable (the table starts empty)."""
    from contextlib import contextmanager

    from app import powerbi

    table = FakeTable()

    @contextmanager
    def connect(settings, read_only=True):
        table.read_only = read_only
        yield table

    monkeypatch.setattr(powerbi, "connect", connect)
    return table


@pytest.fixture(autouse=True)
def project_dirs(tmp_path, monkeypatch):
    """Every test gets its own data/ and logs/ and no real .env values."""
    data = tmp_path / "data"
    monkeypatch.setenv("SQP_DATA_DIR", str(data))
    monkeypatch.setenv("SQP_LOG_DIR", str(tmp_path / "logs"))
    for name in ("SPAPI_LWA_CLIENT_ID", "SPAPI_LWA_CLIENT_SECRET", "SPAPI_LWA_REFRESH_TOKEN", "SPAPI_ENDPOINT",
                 "SPAPI_MARKETPLACE_ID", "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_TABLE", "EXPECTED_ASIN_COUNT",
                 "POWERBI_DB_HOST", "POWERBI_DB_USER", "POWERBI_DB_PASSWORD", "POWERBI_DB_TABLE",
                 "SQP_TIMEZONE", "SQP_AVAILABILITY_LAG_DAYS", "SQP_CURRENCY", "LOG_LEVEL"):
        monkeypatch.setenv(name, "")
    return data
