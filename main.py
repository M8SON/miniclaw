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


def run_voice_mode(orchestrator):
    """Run the assistant in voice mode with microphone input."""
    from core.voice import VoiceInterface

    voice = VoiceInterface(
        whisper_model=os.getenv("WHISPER_MODEL", "base"),
        enable_tts=os.getenv("ENABLE_TTS", "true").lower() == "true",
        tts_model_path=os.getenv(
            "TTS_MODEL_PATH", "/app/en_GB-cori-medium.onnx"
        ),
        silence_threshold=int(os.getenv("SILENCE_THRESHOLD", "1000")),
        silence_duration=float(os.getenv("SILENCE_DURATION", "2.0")),
    )

    print("\n" + "=" * 60)
    print("  MiniClaw")
    print("=" * 60)
    print("\n  Speak naturally. I will respond when you finish.")
    print("  Say 'goodbye' or 'stop' to exit.")
    print("  Press Ctrl+C to quit.\n")
    print("=" * 60)

    # List loaded skills
    skills = orchestrator.list_skills()
    if skills:
        print(f"\n  Loaded {len(skills)} skill(s):")
        for s in skills:
            fmt_tag = "native" if s["format"] == "native" else "openclaw"
            print(f"    - {s['name']} [{fmt_tag}]")
    print()

    try:
        while True:
            # Listen
            print("Listening...")
            transcription = voice.listen()

            if not transcription:
                continue

            print(f"You: {transcription}")

            # Check for exit
            exit_words = ["goodbye", "exit", "quit", "stop"]
            if any(word in transcription.lower() for word in exit_words):
                print("\nAssistant: Goodbye!")
                voice.speak("Goodbye!")
                break

            # Process through orchestrator
            response = orchestrator.process_message(transcription)
            print(f"Assistant: {response}\n")

            # Speak
            voice.speak(response)

    except KeyboardInterrupt:
        print("\n\nShutting down...")


def run_text_mode(orchestrator):
    """Run the assistant in text-only mode (no microphone needed)."""
    print("\n" + "=" * 60)
    print("  MiniClaw (Text Mode)")
    print("=" * 60)

    skills = orchestrator.list_skills()
    if skills:
        print(f"\n  Loaded {len(skills)} skill(s):")
        for s in skills:
            fmt_tag = "native" if s["format"] == "native" else "openclaw"
            print(f"    - {s['name']} [{fmt_tag}]")

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

    print(f"\n{'Name':<20} {'Format':<12} {'Directory':<40} Description")
    print("-" * 100)
    for s in skills:
        print(f"{s['name']:<20} {s['format']:<12} {s['dir']:<40} {s['description'][:40]}")
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
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
        skill_paths=skill_paths,
        container_memory=os.getenv("CONTAINER_MEMORY", "256m"),
    )

    # Run in requested mode
    if args.list:
        list_skills(orchestrator)
    elif args.text:
        run_text_mode(orchestrator)
    else:
        run_voice_mode(orchestrator)


if __name__ == "__main__":
    main()
