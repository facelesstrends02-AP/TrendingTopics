"""
write_strategy_to_sheet.py — Write channel strategy to Google Sheets "Strategy" tab

Creates or overwrites a "Strategy" tab in the existing GOOGLE_SHEET_ID workbook.
Unlike analytics/ideas tabs (which append weekly), this tab is overwritten each run
so the sheet always reflects the latest strategy.

Usage:
    python3 tools/write_strategy_to_sheet.py --strategy-file .tmp/channel_strategy.json

Output (stdout): Google Sheet URL
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

# Color palette
COLOR_TITLE_BG = {"red": 0.2, "green": 0.2, "blue": 0.6}       # Deep blue — title banner
COLOR_SECTION_BG = {"red": 0.8, "green": 0.9, "blue": 1.0}     # Light blue — section headers
COLOR_PILLAR_BG = {"red": 0.94, "green": 0.97, "blue": 1.0}    # Very light blue — pillar rows
COLOR_ALT_ROW = {"red": 0.96, "green": 0.96, "blue": 0.96}     # Light gray — alternating rows
COLOR_WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}


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


def get_or_replace_strategy_tab(service, sheet_id):
    """
    Find existing 'Strategy' tab and clear it, or create a new one.
    Returns the internal sheetId (integer).

    Uses clear + reset-formatting instead of delete+recreate to preserve the tab's
    internal sheetId so existing #gid= URL bookmarks remain stable.
    """
    spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = spreadsheet.get("sheets", [])

    for sheet in sheets:
        if sheet["properties"]["title"] == "Strategy":
            tab_id = sheet["properties"]["sheetId"]
            # Clear all cell values
            service.spreadsheets().values().clear(
                spreadsheetId=sheet_id,
                range="Strategy",
            ).execute()
            # Reset all formatting
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": [{"updateCells": {
                    "range": {"sheetId": tab_id},
                    "fields": "userEnteredFormat",
                }}]},
            ).execute()
            print("Cleared existing 'Strategy' tab.", file=sys.stderr)
            return tab_id

    # Create the tab if it doesn't exist
    response = service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": "Strategy"}}}]},
    ).execute()
    tab_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]

    # Delete default "Sheet1" if still present
    for sheet in sheets:
        if sheet["properties"]["title"] == "Sheet1":
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": [{"deleteSheet": {"sheetId": sheet["properties"]["sheetId"]}}]},
            ).execute()
            break

    print("Created 'Strategy' tab.", file=sys.stderr)
    return tab_id


def write_strategy_content(service, sheet_id, tab_id, strategy):
    """Write all strategy content and apply formatting in two API calls."""
    niche = strategy.get("niche", "YouTube")
    generated_at = strategy.get("generated_at", "")
    date_str = generated_at[:10] if generated_at else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    target_audience = strategy.get("target_audience", "")
    goals = strategy.get("goals", "")
    competitors = strategy.get("competitors", [])
    competitors_str = ", ".join(competitors) if competitors else "none specified"

    positioning = strategy.get("channel_positioning", {})
    pillars = strategy.get("content_pillars", [])
    schedule = strategy.get("upload_schedule", {})
    roadmap = strategy.get("roadmap", [])
    milestones = strategy.get("milestone_strategy", {})
    formats = strategy.get("content_formats", [])
    seo_tactics = strategy.get("seo_tactics", [])
    thumbnail_strategy = strategy.get("thumbnail_strategy", "")

    # ── Build row data ────────────────────────────────────────────────────────
    all_rows = []
    row_meta = []  # list of (row_index_0based, format_type)

    def add(row, fmt=None):
        row_meta.append((len(all_rows), fmt))
        all_rows.append(row)

    # Row 1: Title banner
    add([f"CHANNEL STRATEGY — {niche.upper()} — Generated {date_str}"], "title")

    # Row 2: Subtitle
    subtitle = f"Audience: {target_audience}  |  Goals: {goals}  |  Competitors: {competitors_str}"
    add([subtitle], "subtitle")

    # Row 3: Spacer
    add([""], "spacer")

    # Row 4: Channel Positioning section header
    add(["CHANNEL POSITIONING"], "section_header")
    add(["Unique Angle", positioning.get("unique_angle", "")], "label_value")
    add(["Differentiation", positioning.get("differentiation", "")], "label_value")
    add(["Brand Voice", positioning.get("brand_voice", "")], "label_value")
    add([""], "spacer")

    # Content Pillars section
    add(["CONTENT PILLARS"], "section_header")
    add(["#", "Pillar Name", "Description", "Example Title 1", "Example Title 2", "Example Title 3"], "col_header")
    for i, pillar in enumerate(pillars[:4]):
        titles = pillar.get("example_titles", ["", "", ""])
        while len(titles) < 3:
            titles.append("")
        add([str(i + 1), pillar.get("name", ""), pillar.get("description", ""), titles[0], titles[1], titles[2]], "pillar_row")
    add([""], "spacer")

    # Upload Schedule section
    add(["UPLOAD SCHEDULE"], "section_header")
    best_days = ", ".join(schedule.get("best_days", []))
    best_times = ", ".join(schedule.get("best_times_est", []))
    add(["Best Days", best_days], "label_value")
    add(["Best Times (EST)", best_times], "label_value")
    add(["Videos / Week", str(schedule.get("videos_per_week", 3))], "label_value")
    add(["Rationale", schedule.get("rationale", "")], "wrap_value")
    add([""], "spacer")

    # 90-Day Roadmap section
    add(["90-DAY ROADMAP (30 Videos)"], "section_header")
    add(["Week", "Theme", "Video 1", "Video 2", "Video 3", "Weekly Goal"], "col_header")
    for i, week in enumerate(roadmap[:10]):
        videos = week.get("videos", ["", "", ""])
        while len(videos) < 3:
            videos.append("")
        row_fmt = "roadmap_even" if i % 2 == 0 else "roadmap_odd"
        add([str(week.get("week", i + 1)), week.get("theme", ""), videos[0], videos[1], videos[2], week.get("goal", "")], row_fmt)
    add([""], "spacer")

    # Milestone Strategy section
    add(["MILESTONE STRATEGY"], "section_header")
    add(["Days to 1000 Subscribers", milestones.get("days_to_1000_subs", "")], "label_value")
    add(["Days to 4000 Watch Hours", milestones.get("days_to_4000_watch_hours", "")], "label_value")
    add([""], "spacer")
    add(["Fastest Path to 1000 Subscribers"], "subsection_header")
    for j, step in enumerate(milestones.get("fastest_path_1000_subs", [])[:3]):
        add([f"{j + 1}.", step], "label_value")
    add([""], "spacer")
    add(["Fastest Path to 4000 Watch Hours"], "subsection_header")
    for j, step in enumerate(milestones.get("fastest_path_4000_hours", [])[:3]):
        add([f"{j + 1}.", step], "label_value")
    add([""], "spacer")

    # Content Formats section
    add(["CONTENT FORMATS"], "section_header")
    add(["Format", "Why It Works", "Example Title"], "col_header")
    for fmt_item in formats[:4]:
        add([fmt_item.get("format", ""), fmt_item.get("why_it_works", ""), fmt_item.get("example", "")], "label_value")
    add([""], "spacer")

    # SEO Tactics section
    add(["SEO TACTICS"], "section_header")
    for tactic in seo_tactics[:6]:
        add(["•", tactic], "label_value")
    add([""], "spacer")

    # Thumbnail Strategy section
    add(["THUMBNAIL STRATEGY"], "section_header")
    add([thumbnail_strategy], "wrap_full")

    # ── Write all values ──────────────────────────────────────────────────────
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="Strategy!A1",
        valueInputOption="RAW",
        body={"values": all_rows},
    ).execute()

    # ── Build formatting requests ─────────────────────────────────────────────
    NUM_COLS = 6

    def merge(row_0, start_col=0, end_col=None):
        return {
            "mergeCells": {
                "range": {
                    "sheetId": tab_id,
                    "startRowIndex": row_0,
                    "endRowIndex": row_0 + 1,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col or NUM_COLS,
                },
                "mergeType": "MERGE_ALL",
            }
        }

    def repeat_cell(row_0, cell_format, fields, start_col=0, end_col=None):
        return {
            "repeatCell": {
                "range": {
                    "sheetId": tab_id,
                    "startRowIndex": row_0,
                    "endRowIndex": row_0 + 1,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col or NUM_COLS,
                },
                "cell": {"userEnteredFormat": cell_format},
                "fields": fields,
            }
        }

    requests = []

    for row_0, fmt in row_meta:
        if fmt == "title":
            requests.append(merge(row_0))
            requests.append(repeat_cell(row_0, {
                "backgroundColor": COLOR_TITLE_BG,
                "textFormat": {"bold": True, "fontSize": 14, "foregroundColor": COLOR_WHITE},
                "horizontalAlignment": "CENTER",
            }, "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat,userEnteredFormat.horizontalAlignment"))

        elif fmt == "subtitle":
            requests.append(merge(row_0))
            requests.append(repeat_cell(row_0, {
                "textFormat": {"italic": True},
            }, "userEnteredFormat.textFormat"))

        elif fmt == "section_header":
            requests.append(merge(row_0))
            requests.append(repeat_cell(row_0, {
                "backgroundColor": COLOR_SECTION_BG,
                "textFormat": {"bold": True, "fontSize": 11},
            }, "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat"))

        elif fmt == "subsection_header":
            requests.append(merge(row_0))
            requests.append(repeat_cell(row_0, {
                "textFormat": {"bold": True},
            }, "userEnteredFormat.textFormat"))

        elif fmt == "col_header":
            requests.append(repeat_cell(row_0, {
                "textFormat": {"bold": True},
            }, "userEnteredFormat.textFormat"))

        elif fmt == "pillar_row":
            requests.append(repeat_cell(row_0, {
                "backgroundColor": COLOR_PILLAR_BG,
            }, "userEnteredFormat.backgroundColor"))

        elif fmt == "roadmap_even":
            requests.append(repeat_cell(row_0, {
                "backgroundColor": COLOR_WHITE,
            }, "userEnteredFormat.backgroundColor"))

        elif fmt == "roadmap_odd":
            requests.append(repeat_cell(row_0, {
                "backgroundColor": COLOR_ALT_ROW,
            }, "userEnteredFormat.backgroundColor"))

        elif fmt == "wrap_value":
            # Col B onward: wrap text
            requests.append(repeat_cell(row_0, {
                "wrapStrategy": "WRAP",
            }, "userEnteredFormat.wrapStrategy", start_col=1))

        elif fmt == "wrap_full":
            requests.append(merge(row_0))
            requests.append(repeat_cell(row_0, {
                "wrapStrategy": "WRAP",
            }, "userEnteredFormat.wrapStrategy"))

    # Column widths
    col_widths = [200, 320, 200, 200, 200, 220]
    for col_idx, width in enumerate(col_widths):
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": tab_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })

    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": requests},
    ).execute()

    print(f"  {len(all_rows)} rows written, {len(requests)} formatting requests applied.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Write channel strategy to Google Sheet")
    parser.add_argument("--strategy-file", required=True, help="Path to channel_strategy.json")
    args = parser.parse_args()

    if not os.path.exists(args.strategy_file):
        print(f"ERROR: Strategy file not found: {args.strategy_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.strategy_file) as f:
        strategy = json.load(f)

    niche = strategy.get("niche") or os.getenv("NICHE", "YouTube")
    now = datetime.now(timezone.utc)

    service = get_sheets_service()
    sheet_id = get_or_create_workbook(service, niche, now.year)
    tab_id = get_or_replace_strategy_tab(service, sheet_id)
    write_strategy_content(service, sheet_id, tab_id, strategy)

    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}#gid={tab_id}"
    print(f"Strategy written to sheet: {sheet_url}", file=sys.stderr)
    print(sheet_url)


if __name__ == "__main__":
    main()
