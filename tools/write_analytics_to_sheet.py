"""
write_analytics_to_sheet.py — Write video analytics and insights to Google Sheets

Structure: 1 workbook per year ({NICHE} - {YEAR}), monthly tabs (Analytics - Jan, Analytics - Feb, ...),
weekly sections appended with 3-row separators between them.

Usage:
    python3 tools/write_analytics_to_sheet.py \
        --analytics-file .tmp/analytics_2026-03-09.json \
        --insights-file .tmp/analytics_insights.json

Output (stdout): Sheet URL
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/spreadsheets",
]

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "token.json")
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

ANALYTICS_HEADERS = [
    "Week", "Title", "Video ID", "Published", "Views", "Likes", "Comments",
    "Engagement Rate", "Fetched At",
]
NUM_COLS = len(ANALYTICS_HEADERS)  # 9

COLOR_HIGH = {"red": 0.56, "green": 0.93, "blue": 0.56}   # Green
COLOR_MED = {"red": 1.0, "green": 0.95, "blue": 0.4}       # Yellow
COLOR_LOW = {"red": 1.0, "green": 0.6, "blue": 0.6}        # Red

WEEK_HEADER_BG = {"red": 0.8, "green": 0.9, "blue": 1.0}   # light blue


def get_sheets_service():
    if not os.path.exists(TOKEN_PATH):
        print("ERROR: token.json not found. Run setup.sh first.", file=sys.stderr)
        sys.exit(1)
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)


def save_env_values(updates):
    """Update or append multiple key=value pairs in .env."""
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r") as f:
        content = f.read()
    for key, value in updates.items():
        if re.search(rf"^{key}=", content, re.MULTILINE):
            content = re.sub(rf"^{key}=.*$", f"{key}={value}", content, flags=re.MULTILINE)
        else:
            content += f"\n{key}={value}\n"
    with open(ENV_PATH, "w") as f:
        f.write(content)


def get_or_create_workbook(service, niche, year):
    """Return sheet_id for the yearly workbook. Creates a new one if year changed or ID is missing."""
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    sheet_year = os.getenv("GOOGLE_SHEET_YEAR", "").strip()

    if sheet_id and sheet_year == str(year):
        return sheet_id

    title = f"{niche} - {year}"
    print(f"Creating new workbook: '{title}'...", file=sys.stderr)
    body = {"properties": {"title": title}}
    result = service.spreadsheets().create(body=body, fields="spreadsheetId").execute()
    sheet_id = result["spreadsheetId"]

    save_env_values({"GOOGLE_SHEET_ID": sheet_id, "GOOGLE_SHEET_YEAR": str(year)})
    print(f"New workbook created, ID saved to .env: {sheet_id}", file=sys.stderr)
    return sheet_id


def get_or_create_monthly_tab(service, sheet_id, tab_name):
    """Return the internal sheetId of the monthly tab, creating it if needed."""
    spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = spreadsheet.get("sheets", [])

    for sheet in sheets:
        if sheet["properties"]["title"] == tab_name:
            return sheet["properties"]["sheetId"]

    # Create the tab
    response = service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
    ).execute()
    new_tab_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]

    # Delete default "Sheet1" if it still exists
    for sheet in sheets:
        if sheet["properties"]["title"] == "Sheet1":
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": [{"deleteSheet": {"sheetId": sheet["properties"]["sheetId"]}}]},
            ).execute()
            break

    return new_tab_id


def get_next_empty_row(service, sheet_id, tab_name):
    """Find the next empty row in the given tab (1-indexed)."""
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{tab_name}!A:A",
    ).execute()
    values = result.get("values", [])
    return len(values) + 1


def engagement_color(rate):
    if rate >= 0.03:
        return COLOR_HIGH
    elif rate >= 0.015:
        return COLOR_MED
    else:
        return COLOR_LOW


def append_weekly_analytics(service, sheet_id, analytics, insights, now):
    """Append a weekly analytics section to the monthly tab."""
    tab_name = "Analytics - " + now.strftime("%b")
    tab_id = get_or_create_monthly_tab(service, sheet_id, tab_name)

    next_row = get_next_empty_row(service, sheet_id, tab_name)
    is_first = next_row == 1

    # 3 separator rows before each section except the first (use " " so API counts them)
    separator_rows = [[" "], [" "], [" "]] if not is_first else []
    section_start = next_row + (3 if not is_first else 0)  # 1-indexed row where section begins

    week_str = now.strftime("%Y-%m-%d")
    week_label = f"=== Week of {now.strftime('%b %d, %Y')} ==="

    # Build rows: separator + week header + column headers + data
    all_rows = separator_rows + [[week_label], ANALYTICS_HEADERS]
    for v in analytics:
        all_rows.append([
            week_str,
            v.get("title", ""),
            v.get("video_id", ""),
            v.get("published_week", ""),
            v.get("view_count", 0),
            v.get("like_count", 0),
            v.get("comment_count", 0),
            f"{v.get('engagement_rate', 0):.2%}",
            v.get("fetched_at", ""),
        ])

    # Insights block (appended right after data rows)
    if insights:
        all_rows.append([f"--- INSIGHTS for week {week_str} ---"])
        all_rows.append([f"Videos analyzed: {insights.get('videos_analyzed', len(analytics))}"])
        all_rows.append([f"Summary: {insights.get('insights_summary', '')}"])
        if insights.get("double_down_topics"):
            all_rows.append(["Double down on: " + " | ".join(insights["double_down_topics"])])
        if insights.get("avoid_topics"):
            all_rows.append(["Avoid: " + " | ".join(insights["avoid_topics"])])
        for p in insights.get("patterns", []):
            all_rows.append([f"Pattern: {p}"])

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{tab_name}!A{next_row}",
        valueInputOption="RAW",
        body={"values": all_rows},
    ).execute()

    # Formatting (all 0-indexed)
    section_start_0 = section_start - 1       # week header row
    col_header_0 = section_start_0 + 1         # column headers row
    data_start_0 = section_start_0 + 2         # first data row

    requests = []

    # Merge + bold + background for week header
    requests.append({
        "mergeCells": {
            "range": {
                "sheetId": tab_id,
                "startRowIndex": section_start_0,
                "endRowIndex": section_start_0 + 1,
                "startColumnIndex": 0,
                "endColumnIndex": NUM_COLS,
            },
            "mergeType": "MERGE_ALL",
        }
    })
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": tab_id,
                "startRowIndex": section_start_0,
                "endRowIndex": section_start_0 + 1,
            },
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": WEEK_HEADER_BG,
            }},
            "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor",
        }
    })

    # Bold column headers
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": tab_id,
                "startRowIndex": col_header_0,
                "endRowIndex": col_header_0 + 1,
            },
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }
    })

    # Color-code Engagement Rate column (index 7)
    for i, v in enumerate(analytics):
        rate = v.get("engagement_rate", 0)
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": tab_id,
                    "startRowIndex": data_start_0 + i,
                    "endRowIndex": data_start_0 + i + 1,
                    "startColumnIndex": 7,
                    "endColumnIndex": 8,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": engagement_color(rate)}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        })

    # Auto-resize columns
    requests.append({
        "autoResizeDimensions": {
            "dimensions": {"sheetId": tab_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": NUM_COLS}
        }
    })

    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": requests},
    ).execute()

    return tab_id


def main():
    parser = argparse.ArgumentParser(description="Write video analytics to Google Sheet")
    parser.add_argument("--analytics-file", required=True, help="Path to analytics JSON")
    parser.add_argument("--insights-file", required=True, help="Path to insights JSON")
    args = parser.parse_args()

    for f in [args.analytics_file, args.insights_file]:
        if not os.path.exists(f):
            print(f"ERROR: File not found: {f}", file=sys.stderr)
            sys.exit(1)

    with open(args.analytics_file) as f:
        analytics = json.load(f)

    with open(args.insights_file) as f:
        insights = json.load(f)

    niche = os.getenv("NICHE", "YouTube")
    now = datetime.now(timezone.utc)

    service = get_sheets_service()
    sheet_id = get_or_create_workbook(service, niche, now.year)
    tab_id = append_weekly_analytics(service, sheet_id, analytics, insights, now)

    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}#gid={tab_id}"
    print(f"Analytics written to sheet: {sheet_url}", file=sys.stderr)
    print(sheet_url)


if __name__ == "__main__":
    main()
