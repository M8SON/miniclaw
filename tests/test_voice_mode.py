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

    def process_message(self, transcription, on_chunk=None, on_ack_success=None):
        self.processed.append(transcription)
        response = self.responses.pop(0)
        if on_chunk is not None:
            on_chunk(response)
        return response

    def close_session(self):
        return "Goodbye!"

    def start_session(self, mode):
        pass

    def end_session(self):
        pass

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
        self.response_ready_sounds = 0
        self.prebuffer_cues = 0
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1

    def speak_stream_feeder(self, on_first_chunk=None, interruptible=False):
        chunks = []
        first_seen = [False]

        def push(delta: str) -> None:
            if not delta:
                return
            if not first_seen[0]:
                first_seen[0] = True
                if on_first_chunk is not None:
                    on_first_chunk()
            chunks.append(delta)

        def finalize() -> bool:
            if chunks:
                self.spoken.append("".join(chunks))
            chunks.clear()
            return False

        return push, finalize

    def wait_for_wake_word(self):
        if not self.wake_results:
            return False
        return self.wake_results.pop(0)

    def listen(self, max_wait_seconds=0, on_speech_done=None):
        if not self.listen_results:
            return None
        result = self.listen_results.pop(0)
        if result and on_speech_done is not None:
            on_speech_done()
        return result

    def speak(self, text, interruptible=False):
        self.spoken.append(text)
        return False

    def play_startup_sound(self):
        self.startup_sounds += 1

    def play_thinking_sound(self):
        self.thinking_sounds += 1

    def play_response_ready_sound(self):
        self.response_ready_sounds += 1

    def play_prebuffer_cue(self):
        self.prebuffer_cues += 1

    def play_ack_sound(self):
        pass


class VoiceModeTests(unittest.TestCase):
    def test_voice_mode_processes_request_then_exits_on_goodbye(self):
        orchestrator = FakeOrchestrator(["Hello from Kaizen"])
        voice = FakeVoice(
            wake_results=[True],
            listen_results=["tell me something", "goodbye"],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            main.run_voice_mode(orchestrator, voice=voice)

        rendered = output.getvalue()
        self.assertIn("Waiting for wake word", rendered)
        self.assertIn("You: tell me something", rendered)
        self.assertIn("Assistant: Hello from Kaizen", rendered)
        self.assertIn("Assistant: Goodbye!", rendered)
        self.assertEqual(orchestrator.processed, ["tell me something"])
        self.assertEqual(voice.spoken, ["Good morning.", "Hello from Kaizen", "Goodbye!"])
        self.assertEqual(voice.startup_sounds, 1)
        # Pre-buffer cue fires when the first delta arrives — once per
        # streaming turn. Goodbye uses speak() (not streaming), so only the
        # first turn triggers it.
        self.assertEqual(voice.prebuffer_cues, 1)
        self.assertIsNotNone(orchestrator.container_manager._meta_skill_executor)

    def test_voice_mode_plays_thinking_cue_when_speech_endpoints(self):
        orchestrator = FakeOrchestrator(["Response one"])
        voice = FakeVoice(
            wake_results=[True, False],
            listen_results=["tell me something", None],
        )

        with redirect_stdout(io.StringIO()):
            main.run_voice_mode(orchestrator, voice=voice)

        # on_speech_done fires play_thinking_sound the instant the user stops
        # talking — once for the one spoken request, before the response cue.
        self.assertEqual(voice.thinking_sounds, 1)

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


class VoiceModeShutdownTests(unittest.TestCase):
    def test_voice_shutdown_runs_on_normal_exit(self):
        orchestrator = FakeOrchestrator(["Hi"])
        voice = FakeVoice(wake_results=[True], listen_results=["goodbye"])

        with redirect_stdout(io.StringIO()):
            main.run_voice_mode(orchestrator, voice=voice)

        self.assertGreaterEqual(voice.shutdown_calls, 1)

    def test_voice_shutdown_runs_on_keyboard_interrupt(self):
        orchestrator = FakeOrchestrator([])
        voice = FakeVoice(wake_results=[], listen_results=[])

        def raise_kbi():
            raise KeyboardInterrupt
        voice.wait_for_wake_word = raise_kbi

        with redirect_stdout(io.StringIO()):
            main.run_voice_mode(orchestrator, voice=voice)

        self.assertGreaterEqual(voice.shutdown_calls, 1)

    def test_voice_mode_restores_prior_signal_handlers(self):
        import signal as _signal
        orchestrator = FakeOrchestrator(["Hi"])
        voice = FakeVoice(wake_results=[True], listen_results=["goodbye"])

        sentinel_int = _signal.signal(_signal.SIGINT, _signal.SIG_DFL)
        sentinel_term = _signal.signal(_signal.SIGTERM, _signal.SIG_DFL)
        try:
            with redirect_stdout(io.StringIO()):
                main.run_voice_mode(orchestrator, voice=voice)
            self.assertEqual(_signal.getsignal(_signal.SIGINT), _signal.SIG_DFL)
            self.assertEqual(_signal.getsignal(_signal.SIGTERM), _signal.SIG_DFL)
        finally:
            _signal.signal(_signal.SIGINT, sentinel_int)
            _signal.signal(_signal.SIGTERM, sentinel_term)


if __name__ == "__main__":
    unittest.main()
