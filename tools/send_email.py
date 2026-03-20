"""
send_email.py — Send an email via Gmail API

Usage:
    python3 tools/send_email.py --to user@gmail.com --subject "Hello" --body "Message text"

Output (stdout): Gmail message ID
Exit code: 0 on success, 1 on failure
"""

import argparse
import base64
import json
import os
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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
CREDS_PATH = os.path.join(PROJECT_ROOT, "credentials.json")


def get_gmail_service():
    if not os.path.exists(TOKEN_PATH):
        print("ERROR: token.json not found. Run setup.sh first.", file=sys.stderr)
        sys.exit(1)
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def build_message(sender, to, subject, body):
    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


def main():
    parser = argparse.ArgumentParser(description="Send email via Gmail API")
    parser.add_argument("--to", required=True, help="Recipient email address")
    parser.add_argument("--subject", required=True, help="Email subject")
    parser.add_argument("--body", required=True, help="Email body (plain text)")
    args = parser.parse_args()

    sender = os.getenv("GMAIL_FROM")
    if not sender:
        print("ERROR: GMAIL_FROM not set in .env", file=sys.stderr)
        sys.exit(1)

    service = get_gmail_service()
    message = build_message(sender, args.to, args.subject, args.body)

    sent = service.users().messages().send(userId="me", body=message).execute()
    message_id = sent.get("id")
    print(message_id)


if __name__ == "__main__":
    main()
