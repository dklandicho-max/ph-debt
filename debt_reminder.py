#!/usr/bin/env python3
"""Debt & bills reminder, posted to Telegram.

Runs daily (see .github/workflows/payday-reminder.yml) and sends:
  * a due-date alert N days before, and on the day of, every debt/bill payment;
  * a monthly payday summary of all debts and bills.
"""
from __future__ import annotations

import argparse
import calendar
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "debts.yml"
FX_URL = "https://open.er-api.com/v6/latest/MYR"
FX_TIMEOUT = 5
TELEGRAM_TIMEOUT = 10
MONTHS_PH = ["", "Ene", "Peb", "Mar", "Abr", "May", "Hun",
             "Hul", "Ago", "Set", "Okt", "Nob", "Dis"]


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_ym(value) -> tuple[int, int]:
    year, month = str(value).split("-")[:2]
    return int(year), int(month)


def fmt_ym(value) -> str:
    y, m = parse_ym(value)
    return f"{MONTHS_PH[m]} {y}"


def fmt_day(d: date) -> str:
    return f"{MONTHS_PH[d.month]} {d.day}"


def fmt_amount(item: dict) -> str:
    sign = "₱" if item["currency"] == "PHP" else "RM "
    return f"{sign}{item['amount']:,.2f}"


def compute_payday(year: int, month: int, payday_window: list[int]) -> date:
    """First day in payday_window (in list order) that falls on Mon-Fri."""
    for day in payday_window:
        try:
            d = date(year, month, day)
        except ValueError:
            continue  # day doesn't exist in this month
        if d.weekday() < 5:  # Mon=0 .. Fri=4
            return d
    raise ValueError(
        f"No weekday payday found in window {payday_window} for {year}-{month:02d}"
    )


def is_active_in_month(item: dict, year: int, month: int) -> bool:
    ym = (year, month)
    if "starts" in item and ym < parse_ym(item["starts"]):
        return False
    if "ends" in item and ym > parse_ym(item["ends"]):
        return False
    every = int(item.get("every_months", 1))
    if every > 1:
        sy, sm = parse_ym(item["starts"])
        if ((year * 12 + month) - (sy * 12 + sm)) % every != 0:
            return False
    return True


def get_active_debts(debts: list[dict], today: date) -> list[dict]:
    return [d for d in debts if is_active_in_month(d, today.year, today.month)]


def due_date_in_month(item: dict, year: int, month: int) -> date:
    """Due date for the month; clamps day 29-31 to the month's last day."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(int(item["due_day"]), last))


def items_due_on(items: list[dict], target: date) -> list[dict]:
    return [
        it for it in items
        if is_active_in_month(it, target.year, target.month)
        and due_date_in_month(it, target.year, target.month) == target
    ]


def describe(item: dict, on: date) -> str:
    line = f"• {item['name']} — {fmt_amount(item)}"
    if "ends" in item and parse_ym(item["ends"]) == (on.year, on.month):
        line += " (HULING BAYAD 🎉)"
    return line


def format_due_alert(today: date, due_today: list[dict], due_soon: list[dict],
                     soon_date: date) -> str | None:
    if not due_today and not due_soon:
        return None
    lines = [f"⏰ Paalala sa Bayarin — {fmt_day(today)}"]
    if due_today:
        lines += ["", "📌 Due NGAYONG ARAW:"]
        lines += [describe(it, today) for it in due_today]
    if due_soon:
        days = (soon_date - today).days
        lines += ["", f"🗓️ Due sa loob ng {days} araw ({fmt_day(soon_date)}):"]
        lines += [describe(it, soon_date) for it in due_soon]
    return "\n".join(lines)


def fetch_fx_rate(fallback: float, timeout: int = FX_TIMEOUT) -> float:
    """PHP per 1 MYR. Falls back to the configured rate on any failure."""
    try:
        resp = requests.get(FX_URL, timeout=timeout)
        resp.raise_for_status()
        return float(resp.json()["rates"]["PHP"])
    except Exception:
        return fallback


def to_myr(item: dict, php_per_myr: float) -> float:
    return item["amount"] / php_per_myr if item["currency"] == "PHP" else item["amount"]


def format_payday_summary(today: date, debts: list[dict], bills: list[dict],
                          php_per_myr: float) -> str:
    """Payday summary for the coming month (the month after payday)."""
    nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    active_debts = sorted(get_active_debts(debts, nxt), key=lambda d: -d["amount"])
    active_bills = sorted(
        [b for b in bills if is_active_in_month(b, nxt.year, nxt.month)],
        key=lambda b: (int(b["due_day"]), b["name"]),
    )
    debt_myr = sum(to_myr(d, php_per_myr) for d in active_debts)
    bill_myr = sum(to_myr(b, php_per_myr) for b in active_bills)
    debt_php = sum(d["amount"] for d in active_debts)

    lines = [
        f"💰 Payday Reminder — {today.isoformat()}",
        f"Para sa {MONTHS_PH[nxt.month]} {nxt.year}  •  RM 1 = ₱{php_per_myr:,.2f}",
        "",
        f"Utang ngayong buwan: ₱{debt_php:,.2f} (≈ RM {debt_myr:,.2f})",
    ]
    for i, d in enumerate(active_debts, 1):
        due = due_date_in_month(d, nxt.year, nxt.month)
        end = " — HULING BAYAD 🎉" if parse_ym(d["ends"]) == (nxt.year, nxt.month) \
            else f" (hanggang {fmt_ym(d['ends'])})"
        lines.append(f"{i}. {d['name']} — {fmt_amount(d)} • due {fmt_day(due)}{end}")

    lines += ["", f"Bayarin ngayong buwan: ≈ RM {bill_myr:,.2f}"]
    for b in active_bills:
        due = due_date_in_month(b, nxt.year, nxt.month)
        lines.append(f"• {fmt_day(due)} — {b['name']} — {fmt_amount(b)}")

    lines += ["", f"Kabuuan: ≈ RM {debt_myr + bill_myr:,.2f}"]
    if active_debts:
        latest = max(parse_ym(d["ends"]) for d in active_debts)
        months_left = (latest[0] - nxt.year) * 12 + (latest[1] - nxt.month) + 1
        lines.append(
            f"Debt-free sa loob ng {months_left} buwan "
            f"({MONTHS_PH[latest[1]]} {latest[0]})."
        )
    else:
        lines.append("Debt-free ka na! 🎉")
    return "\n".join(lines)


def build_messages(config: dict, today: date, php_per_myr_fn=None) -> list[str]:
    debts = config.get("debts", [])
    bills = config.get("bills", [])
    all_items = debts + bills
    n = int(config.get("remind_days_before", 3))
    soon = today + timedelta(days=n)

    messages = []
    alert = format_due_alert(today, items_due_on(all_items, today),
                             items_due_on(all_items, soon), soon)
    if alert:
        messages.append(alert)

    payday = compute_payday(today.year, today.month, config["payday_window"])
    if today == payday:
        fx = (php_per_myr_fn or fetch_fx_rate)(config["fx_fallback_php_per_myr"])
        messages.append(format_payday_summary(today, debts, bills, fx))
    return messages


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url, json={"chat_id": chat_id, "text": text}, timeout=TELEGRAM_TIMEOUT
    )
    if resp.status_code != 200:
        raise SystemExit(
            f"Telegram sendMessage failed: {resp.status_code} {resp.text}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the message instead of sending it"
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD", help="Override today's date, for testing"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = load_config()

    if args.date:
        today = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        today = datetime.now(ZoneInfo(config["timezone"])).date()

    messages = build_messages(config, today)
    if not messages:
        print(f"{today}: walang paalala ngayong araw.")
        return

    if args.dry_run:
        sys.stdout.reconfigure(encoding="utf-8")
        print("\n\n---\n\n".join(messages))
        return

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_TOKEN and TELEGRAM_CHAT_ID must be set in the environment")
    for text in messages:
        send_telegram(token, chat_id, text)


if __name__ == "__main__":
    main()
