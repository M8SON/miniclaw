"""
Web search skill container - receives a query, returns Brave Search results.
"""

import os
import sys
import json
import requests


def web_search(query: str, count: int = 5) -> str:
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return "Web search not configured: missing API key"

    try:
        response = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            params={"q": query, "count": count},
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            results = []
            for item in data.get("web", {}).get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                })
            return json.dumps(results, indent=2)

        return f"Search failed: HTTP {response.status_code}"

    except Exception as e:
        return f"Search error: {str(e)}"


def main():
    raw_input = os.environ.get("SKILL_INPUT", "")
    if not raw_input:
        raw_input = sys.stdin.read()

    try:
        data = json.loads(raw_input)
        query = data.get("query", "")
    except json.JSONDecodeError:
        query = raw_input.strip()

    if not query:
        print("No search query provided")
        sys.exit(1)

    print(web_search(query))


if __name__ == "__main__":
    main()
