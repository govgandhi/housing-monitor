#!/usr/bin/env python3
"""Housing listing monitor for Georgetown Law summer sublets spreadsheet."""

import csv
import hashlib
import html
import io
import json
import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.request import urlopen

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "seen_listings.json"
LOG_FILE = SCRIPT_DIR / "monitor.log"
ENV_FILE = SCRIPT_DIR / ".env"

TAKEN_KEYWORDS = {"taken", "sublet pending", "pending"}

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


def fetch_csv(sheet_url: str) -> list[dict[str, str]]:
    log.info("Fetching spreadsheet...")
    with urlopen(sheet_url, timeout=30) as resp:
        raw = resp.read().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(raw))
    headers_raw = next(reader)
    # Clean headers and handle quirks
    headers = [h.strip() for h in headers_raw]
    # First column is always Name — header can get corrupted by sheet editors
    if headers:
        headers[0] = "Name"
    # The last column is often empty-named (Status)
    if headers and headers[-1] == "":
        headers[-1] = "Status"
    rows = []
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        # Pad row to match headers length
        while len(row) < len(headers):
            row.append("")
        rows.append({headers[i]: row[i].strip() for i in range(len(headers))})
    log.info(f"Fetched {len(rows)} rows")
    return rows


def parse_rent(rent_str: str) -> float | None:
    """Parse rent string to monthly dollar amount. Returns None if unparseable."""
    if not rent_str:
        return None

    s = rent_str.lower().replace(",", "").replace("$", "").strip()
    # Remove leading $ duplicates (e.g. "$$1700")
    s = s.lstrip("$")

    # Try to find a number, possibly with k suffix
    # Match patterns like: 2.2k, 2400, 1750.00
    match = re.search(r"(\d+(?:\.\d+)?)\s*k\b", s)
    if match:
        amount = float(match.group(1)) * 1000
    else:
        match = re.search(r"(\d+(?:\.\d+)?)", s)
        if not match:
            return None
        amount = float(match.group(1))

    # Check if it's per week
    if "/week" in s or "per week" in s:
        amount *= 4.33

    return amount


def is_taken(row: dict[str, str]) -> bool:
    status = row.get("Status", "").lower()
    dates = row.get("Dates Available", "").lower()
    combined = status + " " + dates
    return any(kw in combined for kw in TAKEN_KEYWORDS)


def is_entire_unit(row: dict[str, str]) -> bool:
    rooms = row.get("Rooms available", "").strip().lower()
    bedrooms = row.get("Bedrooms in Apt", "").strip().lower()

    if "entire unit" in rooms:
        return True

    if "studio" in bedrooms:
        return True

    # Check if rooms available count equals bedrooms in apt count
    rooms_match = re.search(r"(\d+)\s*bedroom", rooms)
    bedrooms_match = re.search(r"(\d+)\s*bedroom", bedrooms)
    if rooms_match and bedrooms_match:
        return rooms_match.group(1) == bedrooms_match.group(1)

    return False


def fingerprint(row: dict[str, str]) -> str:
    key = "|".join([
        row.get("Name", ""),
        row.get("Contact", ""),
        row.get("Rent", ""),
        row.get("Bedrooms in Apt", ""),
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def load_seen() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(hashes: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(hashes), indent=2))


def filter_listings(
    rows: list[dict[str, str]], max_rent: float,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    matching = []
    excluded = []
    for row in rows:
        if is_taken(row):
            continue
        rent = parse_rent(row.get("Rent", ""))
        row["_parsed_rent"] = rent
        entire = is_entire_unit(row)
        reasons = []
        if not entire:
            reasons.append("not entire unit")
        if rent is None:
            reasons.append("rent unparseable")
        elif rent >= max_rent:
            reasons.append(f"rent ${rent:,.0f}")
        if reasons:
            row["_exclude_reason"] = ", ".join(reasons)
            excluded.append(row)
        else:
            matching.append(row)
    matching.sort(key=lambda r: r.get("_parsed_rent") or 0)
    excluded.sort(key=lambda r: r.get("_parsed_rent") or float("inf"))
    log.info(f"Filtered to {len(matching)} matching, {len(excluded)} excluded")
    return matching, excluded


def _build_listing_card(row: dict[str, str]) -> str:
    rent = row.get("_parsed_rent")
    name = html.escape(row.get("Name", "Unknown"))
    rent_raw = html.escape(row.get("Rent", "N/A"))
    bedrooms = html.escape(row.get("Bedrooms in Apt", ""))
    rooms = html.escape(row.get("Rooms available", ""))
    dates = html.escape(row.get("Dates Available", ""))
    contact = html.escape(row.get("Contact", ""))
    desc = html.escape(row.get("Description", ""))

    rent_display = f"${rent:,.0f}/mo" if rent else "???"

    return f"""
        <div style="border:1px solid #ddd; border-radius:8px; padding:16px; margin-bottom:12px; background:#fafafa;">
            <div style="font-size:18px; font-weight:bold; color:#1a1a1a;">{name}</div>
            <div style="color:#2d7d2d; font-size:16px; font-weight:bold; margin:4px 0;">
                {rent_display} <span style="color:#888; font-size:13px; font-weight:normal;">({rent_raw})</span>
            </div>
            <div style="margin:4px 0; color:#555;">
                <strong>{bedrooms}</strong> &mdash; {rooms}
            </div>
            <div style="margin:4px 0; color:#555;">{dates}</div>
            <div style="margin:4px 0;"><strong>Contact:</strong> {contact}</div>
            <div style="margin:8px 0; color:#333; font-size:14px; line-height:1.4;">{desc}</div>
        </div>"""


def _build_excluded_row(row: dict[str, str]) -> str:
    name = html.escape(row.get("Name", "Unknown"))
    rent_raw = html.escape(row.get("Rent", "N/A"))
    bedrooms = html.escape(row.get("Bedrooms in Apt", ""))
    rooms = html.escape(row.get("Rooms available", ""))
    reason = html.escape(row.get("_exclude_reason", ""))
    contact = html.escape(row.get("Contact", ""))

    return f"""
        <tr>
            <td style="padding:6px 8px; border-bottom:1px solid #eee;">{name}</td>
            <td style="padding:6px 8px; border-bottom:1px solid #eee;">{rent_raw}</td>
            <td style="padding:6px 8px; border-bottom:1px solid #eee;">{bedrooms} / {rooms}</td>
            <td style="padding:6px 8px; border-bottom:1px solid #eee;">{contact}</td>
            <td style="padding:6px 8px; border-bottom:1px solid #eee; color:#c44;">{reason}</td>
        </tr>"""


def build_email_html(
    new_listings: list[dict[str, str]],
    new_excluded: list[dict[str, str]] | None = None,
    *,
    max_rent: float,
    sheet_url: str,
) -> str:
    cards = [_build_listing_card(row) for row in new_listings]

    excluded_section = ""
    if new_excluded:
        excluded_rows = "".join(_build_excluded_row(row) for row in new_excluded)
        excluded_section = f"""
        <h3 style="color:#888; margin-top:28px;">Did Not Match Filters ({len(new_excluded)})</h3>
        <p style="color:#999; font-size:13px;">Excluded by rent or unit-type filters. Review in case something was misparsed.</p>
        <table style="width:100%; border-collapse:collapse; font-size:13px; color:#555;">
            <tr style="background:#f0f0f0; text-align:left;">
                <th style="padding:6px 8px;">Name</th>
                <th style="padding:6px 8px;">Rent</th>
                <th style="padding:6px 8px;">Unit</th>
                <th style="padding:6px 8px;">Contact</th>
                <th style="padding:6px 8px;">Reason</th>
            </tr>
            {excluded_rows}
        </table>"""

    # Strip export params for the human-readable link
    view_url = sheet_url.split("/export")[0] if "/export" in sheet_url else sheet_url

    return f"""
    <html><body style="font-family: -apple-system, Arial, sans-serif; max-width:600px; margin:0 auto; padding:16px;">
        <h2 style="color:#1a1a1a;">New Housing Listings</h2>
        <p style="color:#666;">{len(new_listings)} new listing{'s' if len(new_listings) != 1 else ''} under ${max_rent:,.0f}/mo &mdash; entire units only</p>
        {''.join(cards)}
        {excluded_section}
        <hr style="border:none; border-top:1px solid #eee; margin:20px 0;">
        <p style="color:#999; font-size:12px;">
            <a href="{view_url}">View full spreadsheet</a> &bull;
            Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}
        </p>
    </body></html>"""


def send_email(subject: str, html_body: str, env: dict[str, str]) -> None:
    user = env.get("GMAIL_USER", "")
    password = env.get("GMAIL_APP_PASSWORD", "")
    recipients = [
        r.strip() for r in env.get("RECIPIENT_EMAIL", user).split(",") if r.strip()
    ]

    if not user or not password:
        log.error("Missing GMAIL_USER or GMAIL_APP_PASSWORD in .env")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls(context=ctx)
        server.login(user, password)
        server.sendmail(user, recipients, msg.as_string())
    log.info(f"Email sent to {', '.join(recipients)}")


def main() -> None:
    env = load_env()

    sheet_url = env.get("SHEET_URL", "")
    if not sheet_url:
        log.error("Missing SHEET_URL in .env")
        return

    max_rent = float(env.get("MAX_RENT", "3000"))

    rows = fetch_csv(sheet_url)

    # Sanity check: if the sheet returned very few rows but we've seen many
    # before, this is likely a transient Google Sheets export failure.
    seen = load_seen()
    if len(rows) == 0 and len(seen) > 10:
        log.warning(
            f"Spreadsheet returned 0 rows but we have {len(seen)} previously "
            f"seen listings — likely a transient fetch failure, skipping this run"
        )
        return

    matching, excluded = filter_listings(rows, max_rent)
    all_current = matching + excluded
    current_hashes = {fingerprint(row) for row in all_current}

    new_hashes = current_hashes - seen
    new_listings = [row for row in matching if fingerprint(row) in new_hashes]
    new_listings.sort(key=lambda r: r.get("_parsed_rent") or 0)
    new_excluded = [row for row in excluded if fingerprint(row) in new_hashes]
    new_excluded.sort(key=lambda r: r.get("_parsed_rent") or float("inf"))

    log.info(
        f"{len(new_listings)} new matching, {len(new_excluded)} new excluded, "
        f"{len(matching)} total matching"
    )

    send_when_no_new = env.get("SEND_WHEN_NO_NEW", "false").lower() == "true"
    email_kwargs = dict(max_rent=max_rent, sheet_url=sheet_url)

    if new_listings or new_excluded:
        subject = f"🏠 {len(new_listings)} New Housing Listing{'s' if len(new_listings) != 1 else ''}"
        body = build_email_html(new_listings, new_excluded, **email_kwargs)
        send_email(subject, body, env)
    elif send_when_no_new:
        log.info("No new listings, but SEND_WHEN_NO_NEW is set")
        body = build_email_html(matching, **email_kwargs)
        send_email("🏠 Housing Monitor — No New Listings", body, env)
    else:
        log.info("No new listings, skipping email")

    # Save all current hashes (union with old to keep history)
    save_seen(seen | current_hashes)
    log.info("State saved")


if __name__ == "__main__":
    main()
