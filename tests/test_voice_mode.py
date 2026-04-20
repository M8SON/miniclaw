import io
import queue
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import main


class FakeContainerManager:
    def __init__(self):
        self._meta_skill_executor = None


class FakeOrchestrator:
    def __init__(self, responses):
        self.responses = list(responses)
        self.processed = []
        self.container_manager = FakeContainerManager()
        self.scheduled_fire_queue: queue.Queue = queue.Queue()
        self.pending_next_wake_announcements: list[str] = []
        self.speak_callback = None
        self.is_conversation_active = lambda: False
        self._conversation_active_flag = [False]

    def drain_pending_announcements(self):
        drained = list(self.pending_next_wake_announcements)
        self.pending_next_wake_announcements.clear()
        return drained

    def process_scheduled_fire(self, fire):
        pass

    def list_skills(self):
        return [{"name": "skill_tells_random", "description": "Tell a random joke"}]

    def process_message(self, transcription):
        self.processed.append(transcription)
        return self.responses.pop(0)

    def close_session(self):
        return "Goodbye!"

    def inject_startup_context(self, context: str):
        pass

    def greet(self):
        return "Good morning."


class FakeVoice:
    def __init__(self, wake_results, listen_results):
        self.wake_results = list(wake_results)
        self.listen_results = list(listen_results)
        self.spoken = []
        self.startup_sounds = 0
        self.thinking_sounds = 0

    def wait_for_wake_word(self):
        if not self.wake_results:
            return False
        return self.wake_results.pop(0)

    def listen(self, max_wait_seconds=0):
        if not self.listen_results:
            return None
        return self.listen_results.pop(0)

    def speak(self, text):
        self.spoken.append(text)

    def play_startup_sound(self):
        self.startup_sounds += 1

    def play_thinking_sound(self):
        self.thinking_sounds += 1


class VoiceModeTests(unittest.TestCase):
    def test_voice_mode_processes_request_then_exits_on_goodbye(self):
        orchestrator = FakeOrchestrator(["Hello from MiniClaw"])
        voice = FakeVoice(
            wake_results=[True],
            listen_results=["tell me something", "goodbye"],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            main.run_voice_mode(orchestrator, voice=voice)

        rendered = output.getvalue()
        self.assertIn("Waiting for wake phrase", rendered)
        self.assertIn("You: tell me something", rendered)
        self.assertIn("Assistant: Hello from MiniClaw", rendered)
        self.assertIn("Assistant: Goodbye!", rendered)
        self.assertEqual(orchestrator.processed, ["tell me something"])
        self.assertEqual(voice.spoken, ["Good morning.", "Hello from MiniClaw", "Goodbye!"])
        self.assertEqual(voice.startup_sounds, 1)
        self.assertEqual(voice.thinking_sounds, 1)
        self.assertIsNotNone(orchestrator.container_manager._meta_skill_executor)

    def test_voice_mode_ends_idle_session_and_returns_to_wake_loop(self):
        orchestrator = FakeOrchestrator(["Response one"])
        voice = FakeVoice(
            wake_results=[True, False],
            listen_results=[None],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            main.run_voice_mode(orchestrator, voice=voice)

        rendered = output.getvalue()
        self.assertIn("Session ended.", rendered)
        self.assertEqual(orchestrator.processed, [])
        self.assertEqual(voice.spoken, ["Good morning."])  # greeting fires before wake loop
        self.assertEqual(voice.thinking_sounds, 0)

    def test_text_mode_prints_immediate_schedule_output_before_prompt(self):
        orchestrator = FakeOrchestrator([])
        fire = SimpleNamespace(entry=SimpleNamespace(delivery="immediate"))
        orchestrator.scheduled_fire_queue.put(fire)

        def fake_process_scheduled_fire(pending_fire):
            self.assertIs(pending_fire, fire)
            return "Scheduled briefing"

        orchestrator.process_scheduled_fire = fake_process_scheduled_fire

        output = io.StringIO()
        with patch("builtins.input", return_value="quit"), redirect_stdout(output):
            main.run_text_mode(orchestrator)

        rendered = output.getvalue()
        self.assertIn("[scheduled] Scheduled briefing", rendered)


if __name__ == "__main__":
    unittest.main()
