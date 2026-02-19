# Housing Monitor

Monitors a Google Sheets spreadsheet for new housing listings and sends email alerts when new ones appear. Filters by rent ceiling and entire-unit availability.

## Setup

1. Clone the repo and create your config:
   ```bash
   cp .env.example .env
   ```

2. Fill in `.env`:
   - `SHEET_URL` — Google Sheets CSV export URL (use `/export?format=csv&gid=0`)
   - `MAX_RENT` — maximum monthly rent filter
   - `GMAIL_USER` / `GMAIL_APP_PASSWORD` — Gmail credentials ([create an App Password](https://myaccount.google.com/apppasswords))
   - `RECIPIENT_EMAIL` — comma-separated list of recipients

3. Run it:
   ```bash
   python monitor.py
   ```

## Spreadsheet Format

The monitor expects a Google Sheet with these columns:

| Column | Purpose |
|--------|---------|
| Name | Listing poster's name |
| Bedrooms in Apt | Total bedrooms (e.g. "2 bedroom", "Studio") |
| Rooms available | What's being offered (e.g. "Entire unit", "1 bedroom") |
| Dates Available | Availability window |
| Rent | Monthly rent (handles `$`, `k` suffix, `/week`) |
| Contact | Contact info |
| Description | Listing details |
| Status | If it contains "taken" or "pending", the listing is skipped |

## Scheduling (macOS)

Example launchd plists are included. To set up:

1. Edit `com.example.housing-monitor.plist.example` with your paths
2. Copy to `~/Library/LaunchAgents/` (rename without `.example`)
3. Load it:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.youruser.housing-monitor.plist
   ```

The example is configured to run every 3 hours while your machine is awake.

## Health Check

`healthcheck.py` verifies the spreadsheet is accessible, has expected columns, and the state file is valid. It emails an alert if anything fails.

## How It Works

- Fetches the spreadsheet as CSV
- Filters for entire units under the rent ceiling, skipping taken/pending listings
- Fingerprints each listing (name + contact + rent + bedrooms) to track what's been seen
- Emails only new listings, with excluded ones shown separately for review
- Skips the run if the sheet returns 0 rows but we have prior state (guards against transient Google Sheets failures)
