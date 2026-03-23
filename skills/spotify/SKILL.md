---
name: spotify_play
description: Play music on Spotify. Search for and play a song, artist, or album.
requires:
  env:
    - SPOTIFY_CLIENT_ID
    - SPOTIFY_CLIENT_SECRET
---

# Spotify Skill

## When to use
Use this skill when the user asks to play music, a song, an artist,
or an album. Also use for playback control like pause, resume, skip,
or go back.

## Inputs

```yaml
type: object
properties:
  query:
    type: string
    description: Song name, artist, or album to play (e.g., 'Bohemian Rhapsody', 'play some jazz')
required:
  - query
```

## How to respond
Confirm what you are playing. Example: "Playing Bohemian Rhapsody by Queen."
If no matching track is found, let the user know and suggest trying
different search terms.
