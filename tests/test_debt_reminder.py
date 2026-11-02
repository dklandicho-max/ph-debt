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


CONFIG = dr.load_config()
ALL = CONFIG["debts"] + CONFIG["bills"]


def names(items):
    return {i["name"] for i in items}


def test_gloan_c_active_through_december_only():
    debts = CONFIG["debts"]
    assert "GCash GLoan C" in names(dr.get_active_debts(debts, date(2026, 12, 1)))
    assert "GCash GLoan C" not in names(dr.get_active_debts(debts, date(2027, 1, 1)))


def test_due_day_30_clamps_to_end_of_february():
    bpi = {"name": "BPI", "amount": 1, "currency": "PHP", "due_day": 30, "ends": "2028-08"}
    assert dr.due_date_in_month(bpi, 2027, 2) == date(2027, 2, 28)
    assert dr.due_date_in_month(bpi, 2028, 2) == date(2028, 2, 29)
    assert dr.items_due_on([bpi], date(2027, 2, 28)) == [bpi]


def test_quarterly_therapy_months():
    t = [b for b in CONFIG["bills"] if "therapy" in b["name"]][0]
    active = [(y, m) for y in (2026, 2027) for m in range(1, 13) if dr.is_active_in_month(t, y, m)]
    assert active == [(2026, 12), (2027, 3), (2027, 6), (2027, 9), (2027, 12)]


def test_alert_three_days_before_gloan_c():
    msgs = dr.build_messages(CONFIG, date(2026, 11, 8), lambda f: 15.73)
    assert len(msgs) == 1
    assert "GCash GLoan C" in msgs[0] and "Nob 11" in msgs[0]


def test_alert_on_due_day_and_last_payment_flag():
    msgs = dr.build_messages(CONFIG, date(2026, 12, 11), lambda f: 15.73)
    assert "Due NGAYONG ARAW" in msgs[0]
    assert "GCash GLoan C" in msgs[0] and "HULING BAYAD" in msgs[0]


def test_first_of_month_bills_due_today():
    msgs = dr.build_messages(CONFIG, date(2026, 12, 1), lambda f: 15.73)
    for n in ("Rent", "MP2 Pag-IBIG", "WOW Presents Plus", "Zoom therapy"):
        assert n in msgs[0]


def test_payday_sends_alert_and_summary():
    # 2026-11-26 is a Thursday (payday). Nov 29 is 3 days later: Marvin, SMART, CelcomDigi.
    msgs = dr.build_messages(CONFIG, date(2026, 11, 26), lambda f: 15.73)
    assert len(msgs) == 2
    assert "Marvin" in msgs[0] and "Nob 29" in msgs[0]
    summary = msgs[1]
    assert "Para sa Dis 2026" in summary
    assert "GCash GLoan C" in summary and "HULING BAYAD" in summary
    assert "Zoom therapy" in summary


def test_quiet_day_sends_nothing():
    assert dr.build_messages(CONFIG, date(2026, 11, 20), lambda f: 15.73) == []


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


def test_payday_summary_after_gloan_c():
    msg = dr.format_payday_summary(date(2026, 12, 28), CONFIG["debts"], CONFIG["bills"], 15.73)
    assert "Para sa Ene 2027" in msg
    assert "GCash GLoan C" not in msg
    assert "Debt-free sa loob ng 23 buwan (Nob 2028)." in msg
