# Music Transport Controls — Design Spec

**Date:** 2026-04-25
**Status:** Design approved; implementation plan to follow.
**Closes gap:** "Voice control for music stop/pause is still not exposed cleanly" (WORKING_MEMORY.md known gap, dating back several months).

## Goal

Voice transport control for SoundCloud playback — play, stop, pause, resume, skip, volume up/down. Regex-dispatched from STT via the existing `TierRouter` so the LLM never gets involved for these intents. Each transport command takes <50ms from STT to action. Play queries fetch a 20-track queue so vague requests like "play country" produce continuous playback rather than a single track.

## Why now

Mason has the Pi hardware. Voice transport is one of the highest-value daily-use commands. Regex dispatch through the already-built `TierRouter` makes it instant. Activating Ollama on the Pi turns on the tier router; Mason will do that separately. The pieces all exist and just need to be wired up.

## In scope

- Soundcloud handler gains `pause`, `resume`, `skip`, `volume_up`, `volume_down` actions in addition to existing `play` / `stop`.
- mpv runs with `--input-ipc-server=/tmp/miniclaw-mpv.sock` so it can be controlled while alive. Pause / resume / skip / volume operate via the socket. Stop still terminates the process.
- Each `play` query fetches 20 results from SoundCloud (`yt-dlp scsearch20:query`). All 20 URLs are passed to mpv as positional args; mpv plays them sequentially. Skip advances to the next track in the queue. Hardcoded depth of 20.
- `intent_patterns.yaml`: rename stale `soundcloud_play` → `soundcloud`, split the existing single stop/pause/halt regex into separate dispatch entries, add resume and skip patterns.
- `skills/soundcloud/SKILL.md`: expose the action enum and routing hints so Claude routes correctly when the regex doesn't match (LLM fallback path).

## Out of scope (v1)

- **Live `now_playing.json` updates** as mpv advances through the queue. Would require an mpv event-listener thread; defer. The dashboard music widget will show the first track until the next `play` command refreshes it.
- **Skip-back / previous-track.** YAGNI unless explicitly requested.
- **Cross-restart recovery.** If MiniClaw restarts mid-playback, mpv keeps running but `_mpv_process` reference is lost. Document; defer to a follow-up that uses the socket file's existence to detect a live mpv.
- **Playback position seek.** Not a transport-control primitive; out of scope.
- **Queue replenishment.** When a 20-track queue exhausts, playback ends. User says "play country" again. No auto-fetch of the next 20.
- **Configuration knobs** for queue depth, volume step size, socket path. All hardcoded in v1; promote to env vars only if real usage demonstrates need.

## Architecture

### Handler (`core/container_manager._execute_soundcloud`)

`action` is the discriminator. Default is `play` for backward compat with the current behavior.

| Action | Behavior |
|---|---|
| `play` | yt-dlp `scsearch20:query` → spawn mpv with IPC socket and 20 URLs → write `now_playing.json` with the first track's title → return `"Now playing: <first track>"`. If music already playing, terminate it first. |
| `stop` | Terminate mpv process → unlink `/tmp/miniclaw-mpv.sock` → unlink `~/.miniclaw/now_playing.json` → return `"Stopped."`. If nothing is playing, return `"Nothing is playing."`. |
| `pause` | IPC: `{"command": ["set_property", "pause", true]}` → return `"Paused."`. If socket doesn't exist, return `"Nothing is playing."`. |
| `resume` | IPC: `{"command": ["set_property", "pause", false]}` → return `"Resumed."`. Socket-missing same as above. |
| `skip` | IPC: `{"command": ["playlist-next"]}` → return `"Skipped."`. Socket-missing same as above. |
| `volume_up` | IPC: `{"command": ["add", "volume", 5]}` → return `"Volume up."`. Socket-missing same as above. |
| `volume_down` | IPC: `{"command": ["add", "volume", -5]}` → return `"Volume down."`. Socket-missing same as above. |

### IPC helper (`_send_mpv_command`)

New private helper on `ContainerManager`. Connects to the Unix socket, sends a single JSON command (newline-terminated), reads one line of response. Returns the parsed response dict or `None` on any failure (socket missing, connect timeout, malformed JSON).

```python
def _send_mpv_command(self, args: list) -> dict | None:
    sock_path = "/tmp/miniclaw-mpv.sock"
    if not os.path.exists(sock_path):
        return None
    payload = json.dumps({"command": args}).encode() + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(sock_path)
            s.sendall(payload)
            response = s.recv(4096).decode(errors="replace")
        return json.loads(response.split("\n")[0]) if response else None
    except (OSError, json.JSONDecodeError):
        return None
```

### yt-dlp output parsing

`yt-dlp --get-title --get-url -f bestaudio --no-playlist scsearch20:query` returns title and URL on alternating lines. With 20 results: 40 lines. Parse pairwise. If the result count is odd or zero, treat as a search failure and return `"No results found for '<query>' on SoundCloud."`.

The first track's title is written to `~/.miniclaw/now_playing.json`. The 20 URLs are passed to mpv as positional args.

### `skills/soundcloud/SKILL.md`

```yaml
---
name: soundcloud
description: Play, stop, pause, resume, skip, or adjust volume on SoundCloud music. A play
  request queues 20 tracks matching the query; subsequent skip commands advance through
  the queue.
---
# SoundCloud Skill

## When to use
- Play music: "play [song/artist/genre]", "put on some [genre]", "I want to hear [X]"
- Stop: "stop the music", "stop playing", "halt the audio"
- Pause/resume: "pause the music", "resume", "unpause", "continue music"
- Skip: "skip", "next song", "skip this track"
- Volume: "volume up", "louder", "turn it down"

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
For play, confirm the genre/song. For stop/pause/resume/skip/volume, brief
acknowledgement ("Stopped.", "Paused.", "Resumed.", "Skipped.", "Volume up.").
If nothing is playing for a transport command, say so plainly.
```

### `config/intent_patterns.yaml`

Replace the existing soundcloud-related entries with:

```yaml
dispatch:
  - pattern: "^(stop|halt)(\\s+music|\\s+playing|\\s+audio)?[.!?]?$"
    skill: soundcloud
    args: {action: stop}

  - pattern: "^pause(\\s+music|\\s+playing|\\s+audio)?[.!?]?$"
    skill: soundcloud
    args: {action: pause}

  - pattern: "^(resume|continue|unpause)(\\s+music|\\s+playing|\\s+audio)?[.!?]?$"
    skill: soundcloud
    args: {action: resume}

  - pattern: "^(skip|next)(\\s+song|\\s+track|\\s+this)?[.!?]?$"
    skill: soundcloud
    args: {action: skip}

  - pattern: "^(volume up|turn it up|louder)[.!?]?$"
    skill: soundcloud
    args: {action: volume_up}

  - pattern: "^(volume down|turn it down|quieter|lower the volume)[.!?]?$"
    skill: soundcloud
    args: {action: volume_down}
```

Two changes from the current state: `soundcloud_play` → `soundcloud` (the skill was renamed during the agentskills.io migration but these patterns weren't updated, so the dispatch path was silently broken anyway), and `pause` is split out of the stop pattern so it routes to its distinct action.

## Testing

Unit tests, no real mpv or yt-dlp invocation.

### `tests/test_soundcloud_handler.py` (new)

- IPC helper:
  - Socket file missing → returns `None`.
  - Successful round-trip with mocked socket → returns parsed dict.
  - Malformed JSON response → returns `None`.
  - Connection timeout → returns `None`, doesn't raise.
- Handler dispatch:
  - Each of `pause`, `resume`, `skip`, `volume_up`, `volume_down` calls IPC helper with the right command tuple.
  - When socket missing, IPC actions return `"Nothing is playing."`.
  - `stop` terminates the mocked mpv process, unlinks the socket file, unlinks `now_playing.json`.
  - `play` with mocked yt-dlp output (20 title/url pairs) spawns mpv with all 20 URLs and the IPC server flag.
  - `play` with empty yt-dlp output returns the no-results message.
  - `play` while music already playing terminates the existing process before starting the new one.
  - Default action is `play` (backward-compat with current `tool_input` shapes).

### `tests/test_intent_patterns_music.py` (new, or extend `test_tier_router.py`)

- Each new pattern matches its intended phrasings.
- "pause the music" routes to `action: pause`, not `action: stop`.
- "skip" / "next song" / "skip this track" all route to `action: skip`.
- "stop" doesn't accidentally match "stop right there" (anchored to start + optional terminal punctuation).
- All dispatch entries reference `skill: soundcloud` (no stale `soundcloud_play`).

## Risks and mitigations

**mpv IPC socket clean-up.** If mpv crashes without removing the socket, subsequent `play` may fail to bind. Mitigation: in the play branch, before spawning mpv, unlink the socket file if it exists. Already part of the "stop existing process first" path.

**Concurrent IPC commands.** Two voice commands in rapid succession could both try to connect to the socket. mpv's IPC handles this fine (it's request-response per connection); no concurrency issue at our layer.

**yt-dlp latency at queue depth 20.** ~5–8 seconds for 20-result metadata fetch on the Pi vs ~1–2 for 1 result. The user hears nothing during this window; consider a `_speak("getting tracks now")` call before yt-dlp runs to fill the silence. **Decision:** skip in v1 — Mason can add it once he hears the actual delay on his hardware.

**mpv missing or wrong version.** Existing handler already checks `shutil.which("mpv")` and returns an install message. The IPC server flag has been in mpv since 0.7.0 (2014); any reasonable mpv has it.

## Future direction (deferred)

- **Live now-playing updates** via mpv event listener. Background thread reads `event` lines from the socket, watches for `start-file` events, updates `now_playing.json` with the new track's title from `playlist-current-pos`. Brings the dashboard music widget to life as the queue advances.
- **Skip-back** — IPC: `["playlist-prev"]`. Trivial to add. Withhold until you ask for it.
- **Queue replenishment** — when the queue is one track from exhausted, fire another yt-dlp in a thread, append URLs to mpv's playlist via `["loadfile", url, "append"]`. Turns "play country" into truly continuous music. Real value for the long-running session use case.
- **Volume step size as env var** — `SOUNDCLOUD_VOLUME_STEP=5`. Add when you want finer control.
- **Per-session "current queue" memory** — persist the queue + position across MiniClaw restarts so a crash doesn't lose the listening session.
