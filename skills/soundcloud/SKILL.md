---
name: soundcloud
description: Play, stop, pause, resume, skip, or adjust volume on SoundCloud music. A play
  request queues 20 tracks matching the query; subsequent skip commands advance through
  the queue.
---
# SoundCloud Skill

## When to use

- **Play music** — "play [song/artist/genre]", "put on some [genre]", "I want to hear [X]"
- **Stop** — "stop the music", "stop playing", "halt the audio"
- **Pause / resume** — "pause the music", "resume", "unpause", "continue music"
- **Skip** — "skip", "next song", "skip this track"
- **Volume** — "volume up", "louder", "turn it down"

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [play, stop, pause, resume, skip, volume_up, volume_down]
    description: The transport command to issue. Defaults to play.
  query:
    type: string
    description: Song name, artist, or genre. Required when action is play.
required:
  - action
```

## How to respond

For play, confirm the genre/song. For stop / pause / resume / skip / volume, brief
acknowledgement ("Stopped.", "Paused.", "Resumed.", "Skipped.", "Volume up."). If
nothing is playing for a transport command, say so plainly.
