"""Writing a week to brandanalytics_uk, against the in-memory FakeTable (tests/conftest.py)."""
from __future__ import annotations

import datetime as dt

import pytest

from app import config, powerbi, schema
from app.exceptions import DatabaseError, MalformedReport
from app.report import combine
from tests.conftest import FakeTable, make_item, make_report

START, END = dt.date(2026, 10, 11), dt.date(2026, 10, 17)
DAY = dt.date(2026, 10, 17)
MP = "A1F83G8C2ARO7P"


class Batch:
    def __init__(self, report_id, asins, payload):
        self.report_id, self.asins, self.payload = report_id, asins, payload


def week_of(*items, asins=("B0TESTAAAA", "B0TESTBBBB")):
    return combine([Batch("R1", list(asins), make_report(list(items)))], START, END, MP)


def stored(table, item, day=DAY, **overrides):
    """``item`` as the table holds it (a row loaded earlier)."""
    record = week_of(item, asins=(item["asin"],)).parsed.records[0]
    return table.add(**{**powerbi.table_values(record, frozenset(FakeTable.INT_SHARES)), "reporting_date": day,
                        "asin": item["asin"], **overrides})


@pytest.fixture
def settings():
    return config.load_settings()


def test_every_report_metric_has_a_column():
    record = week_of(make_item()).parsed.records[0]
    values = powerbi.table_values(record)
    assert list(values) == list(schema.TABLE_COLUMNS.values()) and len(values) == 33
    # Amazon's percentages and the table's agree (0.025 stays 0.03 %, not 2.5)
    assert values["impressions_asin_share_percent"] == 0.03 and values["clicks_click_rate_percent"] == 0.8
    assert values["purchases_asin_price_median"] == 24.99 and values["search_query_volume"] == 5000
    assert values["basket_adds_total_count"] == 900 and values["clicks_2d_shipping_percent"] == 2000


def test_shares_in_int_columns_are_rounded_half_up_like_mysql():
    item = make_item(cartAddData__asinCartAddShare=16.67, purchaseData__asinPurchaseShare=0.5)
    values = powerbi.table_values(week_of(item).parsed.records[0], frozenset(FakeTable.INT_SHARES))
    assert values["basket_adds_asin_share_percent"] == 17 and values["purchases_asin_share_percent"] == 1
    assert values["clicks_asin_share_percent"] == 0.03  # a FLOAT column keeps its decimals


def test_a_new_week_is_inserted_with_its_date_and_asin(settings, fake_table):
    stored(fake_table, make_item(), day=dt.date(2026, 10, 10))
    plan = powerbi.write_week(settings, week_of(make_item(), make_item(query="Other")))
    assert len(plan.inserts) == 2 and not plan.updates
    new = [r for r in fake_table.rows if r["reporting_date"] == DAY]
    assert len(new) == 2 and {r["asin"] for r in new} == {"B0TESTAAAA"}
    assert set(new[0]) == {"search_query", "reporting_date", "asin", *schema.METRIC_COLUMNS}
    assert fake_table.committed == 1


def test_a_rerun_changes_nothing_and_adds_no_duplicates(settings, fake_table):
    week = week_of(make_item(), make_item(query="Other", cartAddData__asinCartAddShare=16.67))
    powerbi.write_week(settings, week)
    again = powerbi.write_week(settings, week)
    assert (len(again.inserts), len(again.updates), again.unchanged) == (0, 0, 2)  # 17 in the INT column = 16.67
    assert len(fake_table.rows) == 2


def test_matching_rows_are_updated_in_place_and_nothing_else_is_touched(settings, fake_table):
    old = stored(fake_table, make_item())
    other_week = stored(fake_table, make_item(), day=dt.date(2026, 10, 10))
    not_in_report = stored(fake_table, make_item(query="query Amazon dropped"))
    plan = powerbi.write_week(settings, week_of(make_item(searchQueryData__searchQueryVolume=6000)))
    assert [(row, new) for row, new, _ in plan.updates] == [(("B0TESTAAAA", "Garlic Press"),
                                                             {"search_query_volume": 6000})]
    assert old["search_query_volume"] == 6000
    assert other_week["search_query_volume"] == 5000 and not_in_report["search_query_volume"] == 5000
    assert plan.not_in_report == 1 and len(fake_table.rows) == 3


def test_an_empty_amazon_value_never_replaces_a_stored_one(settings, fake_table):
    row = stored(fake_table, make_item())
    plan = powerbi.write_week(settings, week_of(make_item(clickData__totalSameDayShippingClickCount=None)))
    assert row["clicks_same_day_shipping_percent"] == 10 and plan.kept_values == 1 and plan.unchanged == 1


def test_queries_match_whatever_their_case_and_spacing(settings, fake_table):
    stored(fake_table, make_item(query="garlic  PRESS"))
    plan = powerbi.write_week(settings, week_of(make_item(query="Garlic Press")))
    assert plan.unchanged == 1 and not plan.inserts and len(fake_table.rows) == 1


def test_a_changed_row_is_found_by_its_stored_text(settings, fake_table):
    row = stored(fake_table, make_item(query="garlic  PRESS"))
    plan = powerbi.write_week(settings, week_of(make_item(query="Garlic Press", searchQueryData__searchQueryVolume=7)))
    assert plan.updates[0][0] == ("B0TESTAAAA", "garlic  PRESS") and row["search_query_volume"] == 7


def test_a_query_stored_with_question_marks_is_matched_by_its_numbers(settings, fake_table):
    # An earlier load turned special characters into "?": the same query, every number equal.
    damaged = stored(fake_table, make_item(query="24??13??car window shade"))
    plan = powerbi.write_week(settings, week_of(make_item(query="24”x13” car window shade")))
    assert not plan.inserts and plan.unchanged == 1 and plan.not_in_report == 0
    assert plan.text_differs == [(("B0TESTAAAA", "24??13??car window shade"), "24??13??car window shade",
                                  "24”x13” car window shade")]
    assert fake_table.rows == [damaged]  # text kept, no near-duplicate


def test_a_row_broken_by_a_column_shift_is_repaired_not_duplicated(settings, fake_table):
    item = make_item(query="car sun shade 25”")
    good = powerbi.table_values(week_of(item).parsed.records[0], frozenset(FakeTable.INT_SHARES))
    columns = schema.METRIC_COLUMNS
    # What the earlier load stored: score glued to the text, every number one column to the left.
    broken = {columns[i]: good[columns[i + 1]] for i in range(len(columns) - 1)}
    row = stored(fake_table, make_item(), **broken, search_query="car sun shade 25??1")
    plan = powerbi.write_week(settings, week_of(item))
    assert not plan.inserts and [r[0] for r in plan.repairs] == [("B0TESTAAAA", "car sun shade 25??1")]
    assert row["search_query"] == "car sun shade 25”" and all(row[c] == good[c] for c in columns)
    assert len(fake_table.rows) == 1
    assert powerbi.plan_week(settings, week_of(item)).unchanged == 1  # repaired rows read back as unchanged


def test_different_numbers_are_a_different_query(settings, fake_table):
    stored(fake_table, make_item(query="sun shade 25?"))
    plan = powerbi.plan_week(settings, week_of(make_item(query="sun shade 25”", searchQueryData__searchQueryVolume=9)))
    assert len(plan.inserts) == 1 and not plan.text_differs and plan.not_in_report == 1


def test_a_key_on_several_rows_is_skipped_not_guessed(settings, fake_table):
    # The table's first week (2026-02-21) has rows loaded twice: such keys are never written.
    stored(fake_table, make_item())
    stored(fake_table, make_item())
    plan = powerbi.write_week(settings, week_of(make_item(searchQueryData__searchQueryVolume=1)))
    assert plan.ambiguous == [("B0TESTAAAA", "garlic press")] and not plan.updates
    assert {r["search_query_volume"] for r in fake_table.rows} == {5000}


def test_an_update_that_would_reach_several_rows_rolls_the_week_back(settings, fake_table, monkeypatch):
    # MySQL compares text without accents too, so the stored text can find more rows than Python matched.
    first = dict(stored(fake_table, make_item(query="café")))
    second = dict(stored(fake_table, make_item(query="cafe")))
    monkeypatch.setattr(fake_table, "matches", lambda row, day, asin, query: row["reporting_date"] == day)
    with pytest.raises(DatabaseError, match="2 rows match, expected exactly 1; rolled back"):
        powerbi.write_week(settings, week_of(make_item(query="cafe", searchQueryData__searchQueryVolume=1)))
    assert fake_table.rows == [first, second]


def test_a_failure_part_way_rolls_everything_back(settings, fake_table):
    before = dict(stored(fake_table, make_item()))
    fake_table.fail_on = "INSERT INTO `brandanalytics_uk`"  # after the UPDATE
    with pytest.raises(DatabaseError, match="rolled back, nothing changed"):
        powerbi.write_week(settings, week_of(make_item(searchQueryData__searchQueryVolume=1), make_item(query="New")))
    assert fake_table.rows == [before] and fake_table.committed == 0


def test_a_missing_column_stops_before_anything_is_written(settings, fake_table):
    del fake_table.types["basket_adds_total_count"]
    with pytest.raises(DatabaseError, match="no column.*basket_adds_total_count"):
        powerbi.write_week(settings, week_of(make_item()))
    assert fake_table.rows == []


def test_a_dry_run_plan_cannot_write(settings, fake_table):
    stored(fake_table, make_item())
    plan = powerbi.plan_week(settings, week_of(make_item(searchQueryData__searchQueryVolume=1), make_item(query="New")))
    assert len(plan.updates) == 1 and len(plan.inserts) == 1
    assert len(fake_table.rows) == 1 and fake_table.rows[0]["search_query_volume"] == 5000


def test_a_price_in_another_currency_is_refused(settings, fake_table):
    item = make_item(clickData__asinMedianClickPrice={"amount": 9.99, "currencyCode": "USD"})
    with pytest.raises(MalformedReport, match="GBP only"):
        powerbi.write_week(settings, week_of(item))
    assert fake_table.rows == []


def test_no_insert_changes_existing_rows_only(settings, fake_table):
    row = stored(fake_table, make_item())
    plan = powerbi.write_week(settings, week_of(make_item(searchQueryData__searchQueryVolume=6000),
                                                make_item(query="brand new query")), insert=False)
    assert len(plan.inserts) == 1 and len(plan.updates) == 1  # planned, but the insert is not written
    assert fake_table.rows == [row] and row["search_query_volume"] == 6000


def test_large_prices_compare_at_float_precision():
    # MySQL FLOAT returns 31523.45 as 31523.4: the same stored value, not a change.
    assert powerbi._same(31523.4, 31523.45) and powerbi._same(21324.3, 21324.26)
    assert not powerbi._same(24.99, 25.99) and not powerbi._same(0.08, 0.09)
