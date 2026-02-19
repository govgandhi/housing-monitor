#!/usr/bin/env python3
"""Health check for housing monitor — verifies the sheet and monitor are working."""

import csv
import io
import json
import logging
import smtplib
import ssl
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from urllib.request import urlopen

SCRIPT_DIR = Path(__file__).parent
SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1M8lfRF1vb_hR2VG858IZPisG1Y6vOCxEwyQCWdi7YUg/export?format=csv&gid=0"
)
ENV_FILE = SCRIPT_DIR / ".env"
LOG_FILE = SCRIPT_DIR / "monitor.log"
STATE_FILE = SCRIPT_DIR / "seen_listings.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(__name__)


def load_env() -> dict[str, str]:
    env = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def run_checks(sheet_url: str) -> list[str]:
    failures = []

    if not sheet_url:
        failures.append("SHEET_URL not set in .env")
        return failures

    # 1. Sheet is accessible and returns CSV data
    try:
        with urlopen(sheet_url, timeout=30) as resp:
            raw = resp.read().decode("utf-8-sig")
        reader = csv.reader(io.StringIO(raw))
        headers = [h.strip() for h in next(reader)]
        rows = [r for r in reader if any(cell.strip() for cell in r)]
    except Exception as e:
        failures.append(f"Sheet fetch failed: {e}")
        return failures

    # 2. Got a reasonable number of rows
    if len(rows) < 10:
        failures.append(f"Only {len(rows)} rows returned — sheet tab may have shifted")

    # 3. Expected columns exist (City, Rent, etc.)
    expected = {"City", "Bedrooms in Apt", "Rooms available", "Rent", "Contact"}
    header_set = set(headers)
    missing = expected - header_set
    if missing:
        failures.append(f"Missing expected columns: {missing}")

    # 4. First column header isn't "Name" — someone may have overwritten it
    #    (monitor.py forces it, but we flag it as a warning)
    if headers[0].lower() not in ("name", "nie", "ame"):
        failures.append(
            f"First column header is '{headers[0]}' instead of 'Name' "
            f"(monitor handles this, but the sheet header is corrupted)"
        )

    # 5. State file exists and is valid JSON
    if not STATE_FILE.exists():
        failures.append("seen_listings.json is missing")
    else:
        try:
            data = json.loads(STATE_FILE.read_text())
            if not isinstance(data, list):
                failures.append("seen_listings.json is not a list")
        except json.JSONDecodeError as e:
            failures.append(f"seen_listings.json is invalid JSON: {e}")

    # 6. monitor.py exists at expected path
    if not (SCRIPT_DIR / "monitor.py").exists():
        failures.append("monitor.py not found")

    return failures


def send_alert(failures: list[str], env: dict[str, str]) -> None:
    user = env.get("GMAIL_USER", "")
    password = env.get("GMAIL_APP_PASSWORD", "")
    recipient = env.get("HEALTHCHECK_RECIPIENT", env.get("GMAIL_USER", ""))

    if not user or not password:
        log.error("Missing GMAIL_USER or GMAIL_APP_PASSWORD in .env")
        return

    body = "Housing monitor health check failed:\n\n"
    for i, f in enumerate(failures, 1):
        body += f"  {i}. {f}\n"
    body += f"\nTimestamp: {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    msg = MIMEText(body)
    msg["Subject"] = f"\u26a0\ufe0f Housing Monitor Health Check — {len(failures)} issue(s)"
    msg["From"] = user
    msg["To"] = recipient

    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls(context=ctx)
        server.login(user, password)
        server.sendmail(user, [recipient], msg.as_string())
    log.info(f"Health check alert sent to {recipient}")


def main() -> None:
    log.info("Running health check...")
    env = load_env()
    failures = run_checks(env.get("SHEET_URL", SHEET_URL))

    if failures:
        log.warning(f"Health check found {len(failures)} issue(s):")
        for f in failures:
            log.warning(f"  - {f}")
        send_alert(failures, env)
    else:
        log.info("Health check passed — all OK")


if __name__ == "__main__":
    main()
