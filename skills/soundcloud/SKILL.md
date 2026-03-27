---
name: soundcloud_play
description: Play music from SoundCloud. Search for and play a song, artist, or genre.
---

# SoundCloud Skill

## When to use
Use this skill when the user asks to play music, a song, an artist, or a genre.
Also use when the user says things like "put on some jazz" or "play something by Radiohead".

## Inputs

```yaml
type: object
properties:
  query:
    type: string
    description: Song name, artist, or genre to play (e.g., 'Bohemian Rhapsody', 'lofi hip hop', 'Arctic Monkeys')
required:
  - query
```

## How to respond
Confirm what is playing. Example: "Playing Bohemian Rhapsody by Queen."
If no matching track is found, let the user know and suggest trying different search terms.
