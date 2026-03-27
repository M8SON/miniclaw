"""
SoundCloud skill container - searches and plays music via yt-dlp and mpv.
"""

import os
import sys
import json
import subprocess


def search_and_play(query: str) -> str:
    # Get stream URL from SoundCloud via yt-dlp
    search_result = subprocess.run(
        [
            "yt-dlp",
            "--get-title",
            "--get-url",
            "-f", "bestaudio",
            "--no-playlist",
            "--cache-dir", "/tmp/yt-dlp-cache",
            f"scsearch1:{query}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if search_result.returncode != 0 or not search_result.stdout.strip():
        return f"No results found for '{query}' on SoundCloud"

    lines = search_result.stdout.strip().splitlines()
    if len(lines) < 2:
        return f"Could not retrieve stream for '{query}'"

    title = lines[0]
    stream_url = lines[1]

    # Play audio via mpv (non-blocking: starts playback and returns)
    subprocess.Popen(
        ["mpv", "--no-video", "--really-quiet", stream_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return f"Now playing: {title}"


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
        print("No query provided")
        sys.exit(1)

    print(search_and_play(query))


if __name__ == "__main__":
    main()
