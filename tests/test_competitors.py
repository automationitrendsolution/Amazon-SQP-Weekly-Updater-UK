"""Search Query Details pages (Seller Central UK): file names per keyword, parsing, table rows."""
from __future__ import annotations

import datetime as dt
import json

from app.competitors import DETAIL_URL, HEADERS, check_rows, number, page_file, parse_capture, week_rows

END = dt.date(2026, 10, 31)


def make_capture(asin="B0TESTAAAA", query="dehumidifier", competitors=(("Rival dehumidifier 20L", "B0RIVALAAA",
                                                                          "Rival", "£129.99", "1,234", "4.89%",
                                                                          "56", "3.2%"),)):
    """A saved page as capture_week stores it: table rows plus the page's plain text."""
    text = ["Query Metrics", "Search Query", ":", query, "Query Volume", ":", "5,000", "Total Impressions", ":",
            "100,000", "Total Clicks", ":", "4,000", "Click Rate", ":", "4%",
            "ASIN Metrics", "ASIN", ":", asin, "Impressions", ":", "2,500", "Impression Share", ":", "2.5%",
            "Clicks", ":", "120", "Click Share", ":", "3%", *HEADERS]
    for c in competitors:
        text += list(c)
    return {"asin": asin, "search_query": query, "rows": [list(HEADERS), *[list(c) for c in competitors]],
            "pageText": text}


def test_keywords_differing_only_in_punctuation_get_their_own_file(tmp_path):
    first = page_file(tmp_path, "B0TESTAAAA", "car sun-shade")
    first.write_text(json.dumps({"search_query": "car sun-shade"}), encoding="utf-8")
    second = page_file(tmp_path, "B0TESTAAAA", "car sun shade")
    assert second != first and second.name.startswith(first.stem + "_")
    assert page_file(tmp_path, "B0TESTAAAA", "car sun-shade") == first  # the saved one still finds itself


def test_the_page_is_seller_central_uk():
    url = DETAIL_URL.format(asin="B0TESTAAAA", query="dehumidifier", week_end="2026-10-31")
    assert url.startswith("https://sellercentral.amazon.co.uk/") and url.endswith("&country-id=gb")


def test_pound_prices_and_percentages_are_numbers():
    assert (number("£129.99"), number("1,234"), number("4.89%"), number("-")) == (129.99, 1234, 4.89, None)


def test_a_page_becomes_rows_in_the_uk_table_layout():
    rows = week_rows([parse_capture(make_capture())], END)
    assert rows == [{"reporting_date": END, "asin": "B0TESTAAAA", "keyword": "dehumidifier",
                     "title": "Rival dehumidifier 20L", "competitor_asin": "B0RIVALAAA", "competitor_brand": "Rival",
                     "price_median": 129.99, "impressions": 1234, "impression_share": 4.89, "clicks": 56,
                     "click_share": 3.2}]
    assert check_rows(rows, {"B0TESTAAAA"}) == []


def test_rows_for_an_asin_outside_the_list_are_refused():
    rows = week_rows([parse_capture(make_capture())], END)
    assert check_rows(rows, {"B0OTHERAAA"}) == ["B0TESTAAAA / 'dehumidifier' / B0RIVALAAA: ASIN not in config/asins.txt"]


def test_the_uk_pages_sentence_case_labels_are_read():
    # Seller Central UK: "Query metrics", "Search query: halloween decorations", "Product title", ...
    text = ["Query metrics", "Search query: halloween decorations", "Total clicks: 209,524",
            "ASIN metrics", "ASIN:", "B0D5YK4YDH", "Clicks: 0",
            "Product title", "ASIN", "Brand", "Price (median)", "Impressions", "Impression share", "Clicks",
            "Click share",
            "Spooky skeleton", "B0RIVALAAA", "Rival", "£9.99", "12,000", "5.5%", "300", "6.1%"]
    rows = [["Product title", "ASIN", "Brand", "Price (median)", "Impressions", "Impression share", "Clicks",
             "Click share"], ["Spooky skeleton", "B0RIVALAAA", "Rival", "£9.99", "12,000", "5.5%", "300", "6.1%"]]
    parsed = parse_capture({"asin": "B0D5YK4YDH", "search_query": "halloween decorations", "rows": rows,
                            "pageText": text})
    assert parsed["search_query"] == "halloween decorations" and parsed["query_total_clicks"] == 209524
    assert parsed["our_clicks"] == 0
    assert [(c["competitor_asin"], c["price_median"], c["impressions"], c["click_share"])
            for c in parsed["competitors"]] == [("B0RIVALAAA", 9.99, 12000, 6.1)]
