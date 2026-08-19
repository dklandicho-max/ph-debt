#!/usr/bin/env python3
"""Monthly payday debt reminder, posted to Telegram."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "debts.yml"
FX_URL = "https://open.er-api.com/v6/latest/MYR"
FX_TIMEOUT = 5
TELEGRAM_TIMEOUT = 10


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_ym(value: str) -> tuple[int, int]:
    year, month = value.split("-")
    return int(year), int(month)


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


def get_active_debts(debts: list[dict], today: date) -> list[dict]:
    current_ym = (today.year, today.month)
    return [d for d in debts if parse_ym(d["ends"]) >= current_ym]


def fetch_fx_rate(fallback: float, timeout: int = FX_TIMEOUT) -> float:
    """PHP per 1 MYR. Falls back to the configured rate on any failure."""
    try:
        resp = requests.get(FX_URL, timeout=timeout)
        resp.raise_for_status()
        return float(resp.json()["rates"]["PHP"])
    except Exception:
        return fallback


def format_message(
    today: date,
    active_debts: list[dict],
    total_php: float,
    php_per_myr: float,
) -> str:
    total_myr = total_php / php_per_myr
    sorted_debts = sorted(active_debts, key=lambda d: d["amount_php"], reverse=True)

    lines = [
        f"\U0001F4B0 Payday Debt Reminder — {today.isoformat()}",
        "",
        f"Total remaining: ₱{total_php:,.2f} (≈ RM {total_myr:,.2f})",
        "",
        "Debts (by amount):",
    ]
    for i, d in enumerate(sorted_debts, 1):
        lines.append(f"{i}. {d['name']} — ₱{d['amount_php']:,.2f} (ends {d['ends']})")

    latest_end = max(parse_ym(d["ends"]) for d in active_debts)
    months_left = (latest_end[0] - today.year) * 12 + (latest_end[1] - today.month)
    lines.append("")
    lines.append(
        f"Debt-free in {months_left} months (by {latest_end[0]:04d}-{latest_end[1]:02d})."
    )
    return "\n".join(lines)


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

    payday = compute_payday(today.year, today.month, config["payday_window"])
    if today != payday:
        sys.exit(0)

    active_debts = get_active_debts(config["debts"], today)
    total_php = sum(d["amount_php"] for d in active_debts)
    php_per_myr = fetch_fx_rate(config["fx_fallback_php_per_myr"])
    message = format_message(today, active_debts, total_php, php_per_myr)

    if args.dry_run:
        sys.stdout.reconfigure(encoding="utf-8")
        print(message)
        return

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_TOKEN and TELEGRAM_CHAT_ID must be set in the environment")
    send_telegram(token, chat_id, message)


if __name__ == "__main__":
    main()
