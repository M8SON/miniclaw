#!/usr/bin/env python3
"""
Scripted integration harness for the voice conversation loop.

This exercises the real main.run_voice_mode control flow with a fake voice
interface so the wake/listen/respond/session logic can be tested without
microphone or speaker hardware.
"""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import main


class HarnessContainerManager:
    def __init__(self):
        self._meta_skill_executor = None


class HarnessOrchestrator:
    def __init__(self, responses):
        self.responses = list(responses)
        self.processed = []
        self.container_manager = HarnessContainerManager()

    def list_skills(self):
        return [
            {"name": "skill_tells_random", "description": "Tell a random joke"},
            {"name": "get_weather", "description": "Get current weather information"},
        ]

    def process_message(self, transcription):
        self.processed.append(transcription)
        if self.responses:
            return self.responses.pop(0)
        return f"Stub response for: {transcription}"


class ScriptedVoice:
    def __init__(self, wake_results, transcripts):
        self._wake_results = list(wake_results)
        self._transcripts = list(transcripts)
        self.spoken = []
        self.startup_count = 0
        self.thinking_count = 0

    def wait_for_wake_word(self):
        return self._wake_results.pop(0) if self._wake_results else False

    def listen(self, max_wait_seconds=0):
        return self._transcripts.pop(0) if self._transcripts else None

    def speak(self, text):
        self.spoken.append(text)
        print(f"[speak] {text}")

    def play_startup_sound(self):
        self.startup_count += 1
        print("[audio] startup")

    def play_thinking_sound(self):
        self.thinking_count += 1
        print("[audio] thinking")


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Run the scripted voice loop harness")
    parser.add_argument(
        "--message",
        action="append",
        dest="messages",
        default=None,
        help="User message to feed after wake detection. Repeat for multiple turns.",
    )
    args = parser.parse_args()

    messages = args.messages or ["tell me a random joke", "goodbye"]
    voice = ScriptedVoice(wake_results=[True], transcripts=messages)
    responses = [
        "Here is a test harness response.",
        "This second response confirms the follow-up turn worked.",
    ]
    orchestrator = HarnessOrchestrator(responses=responses)

    output = io.StringIO()
    with redirect_stdout(output):
        main.run_voice_mode(orchestrator, voice=voice)

    print(output.getvalue(), end="")
    print(f"[summary] startup_sounds={voice.startup_count} thinking_sounds={voice.thinking_count}")
    print(f"[summary] spoken_messages={len(voice.spoken)}")
    print(f"[summary] processed_messages={len(orchestrator.processed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
