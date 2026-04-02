#!/usr/bin/env python3
"""
MiniClaw - Main entry point

A modular, voice-controlled AI assistant designed for Raspberry Pi.
Uses a skill-based architecture with Docker container execution.

Usage:
  python main.py              # Run voice assistant (default)
  python main.py --list       # List loaded skills
  python main.py --text       # Text-only mode (no microphone)
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("miniclaw")


def _print_loaded_skills(orchestrator):
    skills = orchestrator.list_skills()
    if skills:
        print(f"\n  Loaded {len(skills)} skill(s):")
        for s in skills:
            print(f"    - {s['name']}")


def run_voice_mode(orchestrator):
    """Run the assistant in voice mode with microphone input."""
    from core.voice import VoiceInterface

    wake_phrase = os.getenv("WAKE_PHRASE", "computer")

    voice = VoiceInterface(
        whisper_model=os.getenv("WHISPER_MODEL", "base"),
        wake_model=os.getenv("WAKE_MODEL", "tiny"),
        wake_phrase=wake_phrase,
        enable_tts=os.getenv("ENABLE_TTS", "true").lower() == "true",
        tts_voice=os.getenv("TTS_VOICE", "af_heart"),
        tts_speed=float(os.getenv("TTS_SPEED", "1.2")),
        silence_threshold=int(os.getenv("SILENCE_THRESHOLD", "1000")),
        silence_duration=float(os.getenv("SILENCE_DURATION", "2.0")),
    )

    from core.meta_skill import MetaSkillExecutor
    orchestrator.container_manager._meta_skill_executor = MetaSkillExecutor(
        voice=voice,
        orchestrator=orchestrator,
    )

    voice.play_startup_sound()

    print("\n" + "=" * 60)
    print("  MiniClaw")
    print("=" * 60)
    print(f"\n  Wake phrase: '{wake_phrase}'")
    print("  Say 'goodbye' or 'stop' to exit.")
    print("  Press Ctrl+C to quit.\n")
    print("=" * 60)

    _print_loaded_skills(orchestrator)
    print()

    # How long to wait for follow-up speech before returning to wake word detection
    conversation_idle_timeout = float(os.getenv("CONVERSATION_IDLE_TIMEOUT", "8"))

    try:
        while True:
            # Wait for wake word
            print(f"\nWaiting for wake phrase: '{wake_phrase}'...")
            detected = voice.wait_for_wake_word()
            if not detected:
                break  # Ctrl+C

            print("Listening...")

            # Conversation session — keep listening until idle
            while True:
                transcription = voice.listen(max_wait_seconds=conversation_idle_timeout)

                if not transcription:
                    # No speech within idle timeout — end session
                    print("Session ended.")
                    break

                print(f"You: {transcription}")

                # Check for exit
                exit_words = ["goodbye", "exit", "quit", "stop"]
                if any(word in transcription.lower() for word in exit_words):
                    print("\nAssistant: Goodbye!")
                    voice.speak("Goodbye!")
                    return

                voice.play_thinking_sound()
                response = orchestrator.process_message(transcription)
                print(f"Assistant: {response}\n")
                voice.speak(response)

                print("Listening...")

    except KeyboardInterrupt:
        print("\n\nShutting down...")


def run_text_mode(orchestrator):
    """Run the assistant in text-only mode (no microphone needed)."""
    print("\n" + "=" * 60)
    print("  MiniClaw (Text Mode)")
    print("=" * 60)

    _print_loaded_skills(orchestrator)
    print("\n  Type your message. Type 'quit' to exit.\n")

    try:
        while True:
            user_input = input("You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            if user_input.lower() == "/skills":
                for s in orchestrator.list_skills():
                    print(f"  {s['name']} ({s['format']}) - {s['description']}")
                continue

            if user_input.lower() == "/reset":
                orchestrator.reset_conversation()
                print("  Conversation reset.")
                continue

            response = orchestrator.process_message(user_input)
            print(f"Assistant: {response}\n")

    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")


def list_skills(orchestrator):
    """Print loaded skills and exit."""
    skills = orchestrator.list_skills()

    if not skills:
        print("No skills loaded.")
        return

    print(f"\n{'Name':<20} {'Directory':<40} Description")
    print("-" * 80)
    for s in skills:
        print(f"{s['name']:<20} {s['dir']:<40} {s['description'][:40]}")
    print()


def main():
    parser = argparse.ArgumentParser(description="MiniClaw")
    parser.add_argument(
        "--text", action="store_true", help="Run in text-only mode"
    )
    parser.add_argument(
        "--list", action="store_true", help="List loaded skills and exit"
    )
    parser.add_argument(
        "--skills-dir",
        type=str,
        default=None,
        help="Additional skills directory to scan",
    )
    args = parser.parse_args()

    # Validate API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set. Add it to your .env file.")
        sys.exit(1)

    # Build skill search paths
    skill_paths = [
        Path("./skills"),
        Path.home() / ".miniclaw" / "skills",
    ]
    if args.skills_dir:
        skill_paths.insert(0, Path(args.skills_dir))

    # Initialize orchestrator
    from core.orchestrator import Orchestrator

    orchestrator = Orchestrator(
        anthropic_api_key=api_key,
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        skill_paths=skill_paths,
        container_memory=os.getenv("CONTAINER_MEMORY", "256m"),
        conversation_max_messages=int(os.getenv("CONVERSATION_MAX_MESSAGES", "24")),
        conversation_max_tokens=int(os.getenv("CONVERSATION_MAX_TOKENS", "6000")),
        memory_max_tokens=int(os.getenv("MEMORY_MAX_TOKENS", "2000")),
        skill_prompt_max_tokens=int(os.getenv("SKILL_PROMPT_MAX_TOKENS", "4000")),
    )

    # Inject orchestrator reference for native skills that need to reload
    orchestrator.container_manager._orchestrator = orchestrator

    # Run in requested mode
    if args.list:
        list_skills(orchestrator)
    elif args.text:
        run_text_mode(orchestrator)
    else:
        run_voice_mode(orchestrator)


if __name__ == "__main__":
    main()
