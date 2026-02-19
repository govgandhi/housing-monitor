# Housing Monitor

Monitors the [Georgetown Law Summer 2026 Sublets spreadsheet](https://docs.google.com/spreadsheets/d/1M8lfRF1vb_hR2VG858IZPisG1Y6vOCxEwyQCWdi7YUg) for new housing listings and sends email alerts when new ones appear. Filters by rent ceiling and entire-unit availability.

Built for the specific column format of this sheet -- not a general-purpose spreadsheet monitor.

## Setup

1. Clone the repo and create your config:
   ```bash
   cp .env.example .env
   ```

2. Fill in `.env`:
   - `MAX_RENT` -- maximum monthly rent filter (default: 3000)
   - `GMAIL_USER` / `GMAIL_APP_PASSWORD` -- Gmail credentials ([create an App Password](https://myaccount.google.com/apppasswords))
   - `RECIPIENT_EMAIL` -- comma-separated list of recipients

3. Run it:
   ```bash
   python monitor.py
   ```

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

- Fetches the [spreadsheet](https://docs.google.com/spreadsheets/d/1M8lfRF1vb_hR2VG858IZPisG1Y6vOCxEwyQCWdi7YUg) as CSV
- Filters for entire units under the rent ceiling, skipping taken/pending listings
- Fingerprints each listing (name + contact + rent + bedrooms) to track what's been seen
- Emails only new listings, with excluded ones shown separately for review
- Skips the run if the sheet returns 0 rows but we have prior state (guards against transient Google Sheets failures)
