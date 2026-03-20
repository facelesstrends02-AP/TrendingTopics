"""
Example tool template.
All tools should:
- Accept inputs via argparse or direct function call
- Print results to stdout or write to .tmp/
- Exit with code 0 on success, non-zero on failure
"""

import os
from dotenv import load_dotenv

load_dotenv()


def main():
    # Example: read an env variable
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set in .env")

    print("Tool executed successfully.")


if __name__ == "__main__":
    main()
