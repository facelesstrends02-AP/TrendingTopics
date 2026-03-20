"""
parse_approval_email.py — Parse approval/rejection commands from email reply text

Usage:
    python3 tools/parse_approval_email.py --mode ideas --email-body "APPROVE: 1, 3, 7"
    python3 tools/parse_approval_email.py --mode videos --email-body "APPROVE: abc123, def456"
    echo "looks good, approve all" | python3 tools/parse_approval_email.py --mode ideas --stdin

Output (stdout): JSON with parsed result
Exit codes:
    0 — parsed successfully
    2 — ambiguous (caller should send clarification email)
"""

import argparse
import json
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv()

MAX_IDEAS = 10


def strip_quoted_reply(text):
    """Remove quoted email text (everything after 'On ... wrote:' or '>' lines)."""
    lines = text.splitlines()
    clean = []
    for line in lines:
        # Stop at standard email quote markers
        if re.match(r"^On .+ wrote:$", line.strip()) or line.strip().startswith(">"):
            break
        clean.append(line)
    return "\n".join(clean).strip()


def parse_ideas_approval(text):
    """
    Parse idea approval from text.
    Supported formats:
      APPROVE: 1, 3, 7
      APPROVE ALL
      APPROVE: 1-5
      APPROVE ALL EXCEPT: 4, 9
      REJECT: 2, 5
    Returns: {"approved": [1,3,7], "rejected": [2,4], "ambiguous": False}
    """
    text = strip_quoted_reply(text)
    text_upper = text.upper().strip()
    all_ids = list(range(1, MAX_IDEAS + 1))

    # APPROVE ALL EXCEPT: X, Y
    m = re.search(r"APPROVE\s+ALL\s+EXCEPT[:\s]+([\d,\s]+)", text_upper)
    if m:
        excluded = _parse_number_list(m.group(1))
        approved = [i for i in all_ids if i not in excluded]
        return {"approved": approved, "rejected": excluded, "ambiguous": False}

    # APPROVE ALL
    if re.search(r"\bAPPROVE\s+ALL\b", text_upper):
        return {"approved": all_ids, "rejected": [], "ambiguous": False}

    # APPROVE: 1-5 (range)
    m = re.search(r"APPROVE[:\s]+(\d+)\s*[-–]\s*(\d+)", text_upper)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        approved = list(range(start, end + 1))
        rejected = [i for i in all_ids if i not in approved]
        return {"approved": approved, "rejected": rejected, "ambiguous": False}

    # APPROVE: 1, 3, 7
    m = re.search(r"APPROVE[:\s]+([\d,\s]+)", text_upper)
    if m:
        approved = _parse_number_list(m.group(1))
        if approved:
            rejected = [i for i in all_ids if i not in approved]
            return {"approved": approved, "rejected": rejected, "ambiguous": False}

    # REJECT: 2, 5 (implicit: approve the rest)
    m = re.search(r"REJECT[:\s]+([\d,\s]+)", text_upper)
    if m:
        rejected = _parse_number_list(m.group(1))
        approved = [i for i in all_ids if i not in rejected]
        return {"approved": approved, "rejected": rejected, "ambiguous": False}

    # Natural language fallback via Claude Haiku
    result = _claude_fallback_ideas(text)
    if result:
        return result

    return {"approved": [], "rejected": [], "ambiguous": True}


def parse_videos_approval(text):
    """
    Parse video approval from text.
    Supported formats:
      APPROVE: abc123, def456
      APPROVE ALL
      REJECT: abc123
    Returns: {"approved_video_ids": [...], "rejected_video_ids": [...], "ambiguous": False}
    """
    text = strip_quoted_reply(text)
    text_upper = text.upper().strip()

    # APPROVE ALL
    if re.search(r"\bAPPROVE\s+ALL\b", text_upper):
        return {"approved_video_ids": "__ALL__", "rejected_video_ids": [], "ambiguous": False}

    # Extract YouTube video IDs (11-char alphanumeric strings)
    # Also handle just "APPROVE: abc123"
    approved_ids = []
    rejected_ids = []

    approve_match = re.search(r"APPROVE[:\s]+([A-Z0-9_\-,\s]+?)(?:\n|REJECT|$)", text_upper)
    if approve_match:
        approved_ids = _parse_id_list(approve_match.group(1))

    reject_match = re.search(r"REJECT[:\s]+([A-Z0-9_\-,\s]+?)(?:\n|APPROVE|$)", text_upper)
    if reject_match:
        rejected_ids = _parse_id_list(reject_match.group(1))

    if approved_ids or rejected_ids:
        return {
            "approved_video_ids": approved_ids,
            "rejected_video_ids": rejected_ids,
            "ambiguous": False,
        }

    # Natural language fallback
    result = _claude_fallback_videos(text)
    if result:
        return result

    return {"approved_video_ids": [], "rejected_video_ids": [], "ambiguous": True}


def _parse_number_list(s):
    """Parse comma-separated integers from a string."""
    nums = []
    for part in re.split(r"[,\s]+", s.strip()):
        part = part.strip()
        if part.isdigit():
            nums.append(int(part))
    return nums


def _parse_id_list(s):
    """Parse comma-separated YouTube video IDs."""
    ids = []
    for part in re.split(r"[,\s]+", s.strip()):
        part = part.strip()
        if re.match(r"^[A-Za-z0-9_\-]{5,15}$", part):
            ids.append(part)
    return ids


def _claude_fallback_ideas(text):
    """Use Claude Haiku to extract approval intent from free-form text."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        prompt = f"""The user is replying to an email asking them to approve YouTube video ideas numbered 1-10.
Extract which idea numbers they want to approve from their reply.

Reply text:
\"\"\"{text}\"\"\"

Respond ONLY with valid JSON in this exact format:
{{"approved": [1, 3, 7], "rejected": [2, 4, 5, 6, 8, 9, 10], "ambiguous": false}}

If the reply is completely unclear and you cannot determine intent, respond:
{{"approved": [], "rejected": [], "ambiguous": true}}
"""
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        return json.loads(raw)
    except Exception:
        return None


def _claude_fallback_videos(text):
    """Use Claude Haiku to extract video approval intent from free-form text."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        prompt = f"""The user is replying to an email asking them to approve YouTube videos for publishing.
Extract which video IDs or numbers they want to approve or reject.

Reply text:
\"\"\"{text}\"\"\"

Respond ONLY with valid JSON:
{{"approved_video_ids": ["abc123"], "rejected_video_ids": [], "ambiguous": false}}

If they want to approve all, use: {{"approved_video_ids": "__ALL__", "rejected_video_ids": [], "ambiguous": false}}
If completely unclear: {{"approved_video_ids": [], "rejected_video_ids": [], "ambiguous": true}}
"""
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        return json.loads(raw)
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Parse approval email")
    parser.add_argument("--mode", required=True, choices=["ideas", "videos"])
    parser.add_argument("--email-body", help="Email body text")
    parser.add_argument("--stdin", action="store_true", help="Read email body from stdin")
    args = parser.parse_args()

    if args.stdin:
        body = sys.stdin.read()
    elif args.email_body:
        body = args.email_body
    else:
        print("ERROR: Provide --email-body or --stdin", file=sys.stderr)
        sys.exit(1)

    if args.mode == "ideas":
        result = parse_ideas_approval(body)
    else:
        result = parse_videos_approval(body)

    print(json.dumps(result))

    if result.get("ambiguous"):
        sys.exit(2)


if __name__ == "__main__":
    main()
