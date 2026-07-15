# TTS Barge-In (Wake-Word Interrupt) — Design Spec

**Date:** 2026-06-18
**Status:** Approved — ready for implementation plan
**Scope:** Let the user interrupt Jarvis mid-response by saying the wake word
("hey jarvis"). On interrupt, cut TTS playback and drop straight into a fresh
`listen()` to capture the user's command. Voice mode only, `main` branch.

---

## 1. Problem

While Jarvis is speaking a response, the conversation loop is strictly
turn-based: `listen()` → `process_message()` → `finalize()` (playback). Nothing
listens to the mic during playback — the input stream is closed for the whole
TTS window — so there is no way to interrupt a long or wrong answer except to
wait it out.

This is roadmap item #1 ("TTS interruption — stop speaking when the user talks
over the assistant"), previously **shelved** because the naive approach —
detecting *any* speech during playback — is unreliable: without confirmed
acoustic echo cancellation (AEC), the mic hears Jarvis's own voice through the
speaker (plus TV/ambient noise) and false-triggers constantly.

## 2. Key decision — wake word, not VAD

Instead of energy/VAD-based "detect any speech," the interrupt trigger is the
**wake word** run through the existing openWakeWord backend. A keyword spotter
is far more specific than VAD, which dissolves the echo problem:

- Jarvis's own responses never contain "hey jarvis," so its playback won't trip
  the detector.
- Ambient noise / cross-talk won't trip it.
- The AEC question becomes largely moot — we react to a specific phrase, not raw
  energy.

Trade-off accepted: the user interrupts by *saying the wake word* ("Hey
Jarvis—") rather than just talking over Jarvis. This matches the existing mental
model (you already say "hey jarvis" to summon it) and is dramatically more
reliable on a Pi with unknown AEC. Interrupt latency ≈ time to say the phrase
(~0.7–1s).

VAD-based "just start talking" is explicitly **out of scope**; it can be added
later behind a flag if AEC is confirmed on the hardware.

## 3. Goal / success criteria

- Saying "hey jarvis" during a response cuts playback within ~1s and the loop
  immediately listens for the user's next command.
- Reuses the already-loaded `self.wake_backend` — no second model, no extra
  memory footprint.
- Enabled by default with an env kill-switch (`BARGE_IN_ENABLED`); when off,
  behavior is byte-for-byte identical to today.
- No regression to existing wake / listen / TTS behavior or the 520-test suite.
- Degrades gracefully: if the mic can't be opened during playback (device busy /
  no full-duplex), the feature goes silently inactive that turn — never crashes
  the voice loop.

## 4. Architecture (Approach A — cooperative interrupt event)

The cutoff hinges on one sub-problem: Kokoro hands the writer **one chunk per
sentence**, and `stream.write(sentence_audio)` blocks until that whole sentence
drains — so a "check a flag between chunks" cut could be seconds late. The fix
is to write each sentence in small **sub-blocks** and check a shared
`threading.Event` between them, stopping within tens of ms.

Rejected alternatives:
- **`OutputStream.abort()` from the watcher** — aborting a stream while another
  thread is blocked in `stream.write()` is fragile / non-portable, and leaks the
  live stream object out of `speak_stream`.
- **Persistent full-duplex monitor** (one always-on mic + wake detector across
  the whole conversation) — a real refactor of the wake/listen/`_shared_stream`
  machinery; far larger blast radius than this feature warrants.

### 4.1 Component 1 — wake-word watcher (`VoiceInterface`)

A daemon thread that runs only during response playback. Stored as a handle
`self._barge_in = (audio, stream, stop_event, thread)`.

- `_start_barge_in_watcher(interrupt_event)`:
  - No-op if `self.barge_in_enabled` is false.
  - Opens its **own** mic input stream on `self._input_device_index` (same
    format/rate/CHUNK as the wake/listen streams).
  - Calls `self.wake_backend.reset()` first, so leftover features from
    session-start don't fire instantly (same reasoning as
    `wait_for_wake_word`).
  - Spawns a daemon thread: loop reading ~64ms frames → `wake_backend.detect()`;
    on a hit, `interrupt_event.set()` and exit. Loop also exits when its
    `stop_event` is set.
- `_stop_barge_in_watcher()`:
  - Sets `stop_event`, `thread.join(timeout=...)`, closes the mic stream via the
    existing `_close_pyaudio`. Idempotent. Clears `self._barge_in`.

Reusing `self.wake_backend` is safe because the main wake loop is not running
during playback (we are mid-conversation).

### 4.2 Component 2 — interruptible Kokoro playback (`KokoroTTSBackend`)

`speak_stream(chunks, interrupt_event=None)` and `speak(text,
interrupt_event=None)` learn a cooperative stop signal.

`speak_stream` writer change — write each sentence's resampled audio in
~1024-frame sub-blocks; check `interrupt_event` between sub-blocks; on set, stop
writing immediately.

Cooperative shutdown (avoids deadlock on the bounded `audio_q`):
- **Delta loop** (feeds `sentence_q`): on interrupt, stop feeding and put the
  SENTINEL.
- **Synth worker**: on interrupt, stop synthesizing queued sentences (don't burn
  Pi CPU on audio nobody hears), pass the SENTINEL through.
- **Writer**: on interrupt, stop writing audio, then **drain-and-discard**
  `audio_q` until SENTINEL so no thread blocks on `.put()`. Threads join
  cleanly; the `OutputStream` context manager exits normally.

`speak(text, interrupt_event=None)` (non-streaming path) — single-threaded;
write each `_synth_audio` chunk in sub-blocks checking the event.

When `interrupt_event is None`, both methods behave exactly as today.

### 4.3 Component 3 — feeder + loop wiring

- `speak_stream_feeder(on_first_chunk=None, interruptible=False)`:
  - Creates the `interrupt_event` (only when `interruptible` and
    `barge_in_enabled`).
  - On the **first chunk**, fires `on_first_chunk` (the response-ready cue) *and*
    starts the watcher, then ensures the Kokoro thread.
  - Passes `interrupt_event` into `tts_backend.speak_stream`.
  - `finalize()` joins Kokoro, stops the watcher, and **returns whether it was
    interrupted** (bool). (Today it returns None.)
- `speak(text, interruptible=False)`: wraps the watcher around the non-streaming
  path the same way; returns the interrupted bool.

### 4.4 Config

- `BARGE_IN_ENABLED` env, default `true` → `VoiceInterface.__init__(...,
  barge_in_enabled: bool)`, wired in `build_voice_interface`. When false, the
  watcher never starts and `finalize()` returns False.

## 5. Data flow (interrupt path)

```
response playback begins (first Kokoro chunk)
   ├─ watcher thread: mic → wake_backend.detect()        [loops]
   └─ writer thread:  audio_q → stream.write() sub-blocks [paces playback]
"hey jarvis" detected
   → interrupt_event.set()
   → writer stops mid-sentence, drains queues; synth/delta loops wind down
   → finalize() returns interrupted=True
   → main loop falls through to the next listen()  ← captures the command
```

The main loop already runs `listen → respond → listen`, so on interrupt it
naturally proceeds to `listen()`. `main.py` integration is minimal: pass
`interruptible=True` for response playback and read the returned flag.

**History note:** `orchestrator.process_message` returns the *full* generated
response and records it in conversation history regardless of how much was
spoken — so after an interrupt, history reflects the full intended reply. This
is the simplest correct behavior; revisit only if it feels off in practice.

## 6. Scope

In scope (gets barge-in):
- The LLM response playback — streaming path
  (`speak_stream_feeder`/`finalize`, default `LLM_STREAM_TO_TTS=true`) and the
  non-streaming `speak(response)` path.

Out of scope (stays non-interruptible — short, no value, YAGNI):
- Greeting, "before we chat…" reminders, the goodbye/close_session line, and the
  R2-D2 startup / thinking / response-ready / ack cues.
- VAD "just start talking" interrupt.
- A dedicated `BARGE_IN_WAKE_THRESHOLD` (only add if false fires show up on the
  Pi; reuse the existing `WAKE_WORD_THRESHOLD` for now).

## 7. Error handling / degradation

- **Mic stream won't open during playback** (device busy / no simultaneous
  mic+speaker): log a warning, skip the watcher, let playback finish normally.
  Feature goes inactive that turn; never crashes the loop. *Primary hardware
  unknown to validate on the Pi: concurrent mic capture + Kokoro output on the
  XVF3800.*
- **SIGINT mid-playback**: `shutdown()` also tears down `self._barge_in` so the
  watcher mic stream can't strand `/dev/snd` (the Errno -9996 class of bug).
- **Watcher thread won't join**: bounded `join(timeout=...)`, log on overrun,
  proceed.
- **False fire from Jarvis's own audio**: rare (responses don't contain "hey
  jarvis"); `reset()` at watcher start prevents stale-feature fires. Mitigation
  if observed: `BARGE_IN_WAKE_THRESHOLD` (deferred).

## 8. Testing

Unit (pytest, mocking `sounddevice` / `pyaudio` as existing voice tests do):
1. Writer stops promptly when `interrupt_event` is set partway — remaining
   sub-blocks are NOT written.
2. **No-deadlock shutdown** — interrupt mid-stream with a full `audio_q`; all
   threads join and `speak_stream` returns. (Highest-risk piece.)
3. Watcher sets `interrupt_event` when a fake `wake_backend.detect()` returns
   True; leaves it clear when it never fires.
4. `BARGE_IN_ENABLED=false` → no watcher started, `finalize()` returns False,
   behavior identical to current.
5. `finalize()` returns the interrupted bool; `main` reacts (loops to listen).
6. Graceful degrade — watcher stream-open raises → playback completes, warning
   logged, no crash.
7. Full existing voice suite still passes (520 tests).

Manual on-device (Pi — cannot unit-test):
- Say "hey jarvis" mid-response → playback cuts within ~1s and the next
  `listen()` captures the command.
- Confirm simultaneous mic + speaker works on the XVF3800.

## 9. Documentation

- New env var row for `BARGE_IN_ENABLED` in `CLAUDE.md` / README env table.
- Mark roadmap item #1 (TTS interruption) as shipped with the wake-word
  approach.
