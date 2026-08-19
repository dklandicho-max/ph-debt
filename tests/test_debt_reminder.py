import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import debt_reminder as dr

WINDOW = [26, 27, 28]

# Months where the 26th falls on a weekend (verified via `date.weekday()`).
WEEKEND_26_CASES = {
    (2026, 4): 27,   # 26th is Sunday -> 27th (Monday)
    (2026, 7): 27,   # 26th is Sunday -> 27th (Monday)
    (2026, 9): 28,   # 26th is Saturday -> 28th (Monday)
    (2026, 12): 28,  # 26th is Saturday -> 28th (Monday)
    (2027, 6): 28,   # 26th is Saturday -> 28th (Monday)
    (2027, 9): 27,   # 26th is Sunday -> 27th (Monday)
    (2027, 12): 27,  # 26th is Sunday -> 27th (Monday)
}


@pytest.mark.parametrize("year_month,expected_day", WEEKEND_26_CASES.items())
def test_payday_weekend_26th_cases(year_month, expected_day):
    year, month = year_month
    result = dr.compute_payday(year, month, WINDOW)
    assert result == date(year, month, expected_day)
    assert result.weekday() < 5


@pytest.mark.parametrize("year", [2026, 2027])
@pytest.mark.parametrize("month", range(1, 13))
def test_payday_resolution_all_months(year, month):
    result = dr.compute_payday(year, month, WINDOW)

    # Must be one of the configured window days.
    assert result.day in WINDOW
    # Must be a weekday.
    assert result.weekday() < 5
    # Must be the earliest weekday day in the window (window is ascending).
    for day in WINDOW:
        if day < result.day:
            assert date(year, month, day).weekday() >= 5


def test_payday_window_out_of_order_uses_list_order():
    # 2026-08-26 is a Wednesday, so it should win even if listed after a
    # weekend day in the window.
    assert dr.compute_payday(2026, 8, [22, 26]) == date(2026, 8, 26)
    assert dr.compute_payday(2026, 8, [26, 22]) == date(2026, 8, 26)


def test_payday_no_weekday_in_window_raises():
    # 2026-09-05 is a Saturday, 2026-09-06 is a Sunday: no weekday candidates.
    with pytest.raises(ValueError):
        dr.compute_payday(2026, 9, [5, 6])


DEBTS = [
    {"name": "BPI Credit Card", "amount_php": 17808.45, "ends": "2028-08"},
    {"name": "Marvin", "amount_php": 10000.00, "ends": "2028-11"},
    {"name": "GLoan 3", "amount_php": 4465.50, "ends": "2027-01"},
    {"name": "GLoan 4", "amount_php": 1950.17, "ends": "2026-10"},
    {"name": "GLoan 5", "amount_php": 1820.16, "ends": "2026-11"},
    {"name": "GLoan 2", "amount_php": 1755.15, "ends": "2026-10"},
    {"name": "GLoan 1", "amount_php": 1625.14, "ends": "2026-10"},
]


def test_active_debts_drop_expired_mid_timeline():
    # As of Nov 2026, the three debts ending Oct 2026 have dropped out.
    today = date(2026, 11, 26)
    active = dr.get_active_debts(DEBTS, today)
    active_names = {d["name"] for d in active}
    assert active_names == {"BPI Credit Card", "Marvin", "GLoan 3", "GLoan 5"}


def test_active_debts_ends_this_month_is_still_active():
    # A debt ending in the current month is inclusive of its final payment.
    today = date(2026, 10, 26)
    active = dr.get_active_debts(DEBTS, today)
    assert any(d["name"] == "GLoan 4" for d in active)


def test_active_debts_all_expired_returns_empty():
    today = date(2029, 1, 1)
    assert dr.get_active_debts(DEBTS, today) == []


def test_fetch_fx_rate_uses_fallback_on_request_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise dr.requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(dr.requests, "get", boom)
    assert dr.fetch_fx_rate(fallback=15.01) == 15.01


def test_fetch_fx_rate_uses_fallback_on_bad_status(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            raise dr.requests.exceptions.HTTPError("500 server error")

    monkeypatch.setattr(dr.requests, "get", lambda *a, **k: FakeResp())
    assert dr.fetch_fx_rate(fallback=15.01) == 15.01


def test_fetch_fx_rate_uses_fallback_on_missing_key(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"rates": {"USD": 4.7}}  # no PHP key

    monkeypatch.setattr(dr.requests, "get", lambda *a, **k: FakeResp())
    assert dr.fetch_fx_rate(fallback=15.01) == 15.01


def test_fetch_fx_rate_uses_live_value_on_success(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"rates": {"PHP": 15.42}}

    monkeypatch.setattr(dr.requests, "get", lambda *a, **k: FakeResp())
    assert dr.fetch_fx_rate(fallback=15.01) == 15.42


def test_format_message_snapshot():
    today = date(2026, 8, 26)
    active = dr.get_active_debts(DEBTS, today)
    total_php = sum(d["amount_php"] for d in active)
    message = dr.format_message(today, active, total_php, php_per_myr=15.01)

    expected = (
        "\U0001F4B0 Payday Debt Reminder — 2026-08-26\n"
        "\n"
        "Total remaining: ₱39,424.57 (≈ RM 2,626.55)\n"
        "\n"
        "Debts (by amount):\n"
        "1. BPI Credit Card — ₱17,808.45 (ends 2028-08)\n"
        "2. Marvin — ₱10,000.00 (ends 2028-11)\n"
        "3. GLoan 3 — ₱4,465.50 (ends 2027-01)\n"
        "4. GLoan 4 — ₱1,950.17 (ends 2026-10)\n"
        "5. GLoan 5 — ₱1,820.16 (ends 2026-11)\n"
        "6. GLoan 2 — ₱1,755.15 (ends 2026-10)\n"
        "7. GLoan 1 — ₱1,625.14 (ends 2026-10)\n"
        "\n"
        "Debt-free in 27 months (by 2028-11)."
    )
    assert message == expected


def test_format_message_snapshot_after_some_debts_expire():
    today = date(2026, 11, 26)
    active = dr.get_active_debts(DEBTS, today)
    total_php = sum(d["amount_php"] for d in active)
    message = dr.format_message(today, active, total_php, php_per_myr=15.01)

    expected = (
        "\U0001F4B0 Payday Debt Reminder — 2026-11-26\n"
        "\n"
        "Total remaining: ₱34,094.11 (≈ RM 2,271.43)\n"
        "\n"
        "Debts (by amount):\n"
        "1. BPI Credit Card — ₱17,808.45 (ends 2028-08)\n"
        "2. Marvin — ₱10,000.00 (ends 2028-11)\n"
        "3. GLoan 3 — ₱4,465.50 (ends 2027-01)\n"
        "4. GLoan 5 — ₱1,820.16 (ends 2026-11)\n"
        "\n"
        "Debt-free in 24 months (by 2028-11)."
    )
    assert message == expected
