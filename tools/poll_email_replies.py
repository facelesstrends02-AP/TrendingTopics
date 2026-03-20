"""
poll_email_replies.py — Check Gmail inbox for a reply to a specific email thread

Usage:
    python3 tools/poll_email_replies.py \
        --original-message-id "<id@gmail.com>" \
        --since-timestamp "2026-03-08T22:05:00"

Output (stdout): Plain text body of the reply email
Exit code:
    0 — reply found, body printed to stdout
    1 — no reply found yet
    2 — error
"""

import argparse
import base64
import os
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


def get_gmail_service():
    if not os.path.exists(TOKEN_PATH):
        print("ERROR: token.json not found. Run setup.sh first.", file=sys.stderr)
        sys.exit(2)
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def decode_body(payload):
    """Recursively extract plain text from message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    elif payload.get("mimeType", "").startswith("multipart"):
        for part in payload.get("parts", []):
            result = decode_body(part)
            if result:
                return result
    return ""


def timestamp_to_epoch(ts_str):
    """Convert ISO timestamp string to epoch seconds for Gmail query."""
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description="Poll Gmail for reply to a specific message")
    parser.add_argument("--original-message-id", required=True, help="Gmail message ID of the sent email")
    parser.add_argument("--since-timestamp", required=True, help="ISO timestamp — only look for emails after this")
    args = parser.parse_args()

    service = get_gmail_service()
    epoch = timestamp_to_epoch(args.since_timestamp)

    # Get the original message to find its thread ID
    try:
        original = service.users().messages().get(
            userId="me", id=args.original_message_id, format="metadata",
            metadataHeaders=["Message-ID", "Subject"]
        ).execute()
    except Exception as e:
        print(f"ERROR: Could not fetch original message: {e}", file=sys.stderr)
        sys.exit(2)

    thread_id = original.get("threadId")
    if not thread_id:
        print("ERROR: Could not find thread ID for original message.", file=sys.stderr)
        sys.exit(2)

    # Get all messages in this thread
    try:
        thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    except Exception as e:
        print(f"ERROR: Could not fetch thread: {e}", file=sys.stderr)
        sys.exit(2)

    messages = thread.get("messages", [])
    gmail_from = os.getenv("GMAIL_FROM", "").lower()

    for msg in messages:
        # Skip the original outgoing message
        if msg["id"] == args.original_message_id:
            continue

        headers = msg.get("payload", {}).get("headers", [])
        from_header = get_header(headers, "From").lower()
        internal_date = int(msg.get("internalDate", 0)) // 1000  # ms to seconds

        # Must be from someone else (not our own sent email) and after our timestamp
        if gmail_from and gmail_from in from_header:
            continue
        if epoch and internal_date < epoch:
            continue

        # Extract body
        body = decode_body(msg.get("payload", {}))
        if not body.strip():
            continue

        # Mark as read
        try:
            service.users().messages().modify(
                userId="me", id=msg["id"],
                body={"removeLabelIds": ["UNREAD"]}
            ).execute()
        except Exception:
            pass  # Non-fatal

        print(body.strip())
        sys.exit(0)

    # No reply found
    sys.exit(1)


if __name__ == "__main__":
    main()
