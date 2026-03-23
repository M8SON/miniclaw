"""
Spotify skill container - handles playback control via Spotify API.
"""

import os
import sys
import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth


def get_spotify_client():
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    if not client_id or not client_secret:
        return None

    try:
        return spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope="user-modify-playback-state user-read-playback-state",
            requests_timeout=10,
        ))
    except Exception as e:
        return None


def spotify_play(client, query: str) -> str:
    try:
        results = client.search(q=query, limit=1, type="track")

        if results["tracks"]["items"]:
            track = results["tracks"]["items"][0]
            track_name = track["name"]
            artist_name = track["artists"][0]["name"]

            devices = client.devices()
            if not devices["devices"]:
                return "No active Spotify devices found. Open Spotify on a device first."

            client.start_playback(uris=[track["uri"]])
            return f"Now playing: {track_name} by {artist_name}"

        return f"No tracks found matching '{query}'"

    except Exception as e:
        return f"Spotify error: {str(e)}"


def main():
    raw_input = os.environ.get("SKILL_INPUT", "")
    if not raw_input:
        raw_input = sys.stdin.read()

    try:
        data = json.loads(raw_input)
        query = data.get("query", "")
    except json.JSONDecodeError:
        query = raw_input.strip()

    client = get_spotify_client()
    if not client:
        print("Spotify not configured: missing credentials")
        sys.exit(1)

    if not query:
        print("No query provided")
        sys.exit(1)

    print(spotify_play(client, query))


if __name__ == "__main__":
    main()
