import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
import types

from core.container_manager import ContainerManager
from core.memory_provider import MemoryProvider

sys.modules.setdefault("anthropic", types.SimpleNamespace(NOT_GIVEN=object()))

from core.tool_loop import ToolLoop


class MemoryProviderTests(unittest.TestCase):
    def test_vault_backend_keeps_newest_notes_within_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "2026-04-01_one.md").write_text("---\n---\n\nfirst memory\n", encoding="utf-8")
            (vault / "2026-04-02_two.md").write_text("---\n---\n\nsecond memory\n", encoding="utf-8")

            provider = MemoryProvider(vault_path=vault, backend="vault", max_tokens=4)

            self.assertEqual(provider.load_for_prompt(), "second memory")

    def test_mempalace_backend_uses_wakeup_text_when_available(self):
        provider = MemoryProvider(backend="mempalace", max_tokens=100)

        with patch.object(provider.mempalace, "load_wake_up", return_value="L0\nL1 memory"):
            self.assertEqual(provider.load_for_prompt(), "L0\nL1 memory")

    def test_auto_backend_falls_back_to_vault_when_mempalace_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "2026-04-02_pref.md").write_text("---\n---\n\nlikes tea\n", encoding="utf-8")

            provider = MemoryProvider(vault_path=vault, backend="auto", max_tokens=100)

            with patch.object(provider.mempalace, "load_wake_up", return_value=""):
                self.assertEqual(provider.load_for_prompt(), "likes tea")

    def test_auto_backend_prefers_mempalace_when_available(self):
        provider = MemoryProvider(backend="auto", max_tokens=100)

        with patch.object(provider.mempalace, "is_available", return_value=True):
            self.assertTrue(provider.should_use_mempalace())

    def test_recall_for_message_uses_mempalace_search(self):
        provider = MemoryProvider(backend="mempalace", max_tokens=100, recall_max_tokens=50)

        with patch.object(provider.mempalace, "search", return_value="memory hit") as search:
            result = provider.recall_for_message("What project am I working on?")

        self.assertEqual(result, "memory hit")
        search.assert_called_once()


class SaveMemoryTests(unittest.TestCase):
    def test_save_memory_reports_mempalace_mirror_when_auto_backend_detects_it(self):
        manager = ContainerManager()

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "MEMORY_VAULT_PATH": tmp,
                "MEMORY_BACKEND": "auto",
            },
            clear=False,
        ), patch.object(manager, "_should_mirror_memory_to_mempalace", return_value=True), patch.object(
            manager, "_save_memory_to_mempalace", return_value=True
        ):
            result = manager._execute_save_memory(
                {"topic": "favorite tea", "content": "User likes earl grey"}
            )

        self.assertIn("filed to MemPalace", result)

    def test_save_memory_explicit_disable_prevents_mirroring(self):
        manager = ContainerManager()

        with patch.dict(
            "os.environ",
            {
                "MEMORY_BACKEND": "auto",
                "MEMPALACE_SAVE_MEMORY": "false",
            },
            clear=False,
        ):
            self.assertFalse(manager._should_mirror_memory_to_mempalace())

    def test_save_memory_auto_backend_mirrors_when_mempalace_available(self):
        manager = ContainerManager()

        with patch.dict("os.environ", {"MEMORY_BACKEND": "auto"}, clear=False), patch(
            "core.container_manager.MemPalaceBridge.is_available",
            return_value=True,
        ):
            self.assertTrue(manager._should_mirror_memory_to_mempalace())


class FakeSkillLoader:
    def get_tool_definitions(self):
        return []


class FakeConversationState:
    def __init__(self):
        self.messages = []

    def append_user_text(self, text):
        self.messages.append({"role": "user", "content": text})

    def append_assistant_content(self, content):
        self.messages.append({"role": "assistant", "content": content})

    def append_tool_results(self, tool_results):
        self.messages.append({"role": "user", "content": tool_results})

    def select_messages_for_prompt(self):
        return list(self.messages)

    def prune(self):
        return None


class FakeResponse:
    def __init__(self, text):
        self.stop_reason = "end_turn"
        self.content = [type("TextBlock", (), {"type": "text", "text": text})()]
        self.usage = type("Usage", (), {"input_tokens": 10, "output_tokens": 5})()


class FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse("hello")


class ToolLoopRecallTests(unittest.TestCase):
    def test_run_appends_relevant_memory_recall_to_system_prompt(self):
        client = FakeClient()
        memory_provider = type(
            "MemoryProviderStub",
            (),
            {"recall_for_message": lambda self, user_message: "memory hit"},
        )()
        loop = ToolLoop(
            client=client,
            model="test-model",
            skill_loader=FakeSkillLoader(),
            container_manager=None,
            conversation_state=FakeConversationState(),
            memory_provider=memory_provider,
        )

        loop.run("where did we leave off", system_prompt="base prompt")

        self.assertIn("Relevant Memory Recall", client.calls[0]["system"])
        self.assertIn("memory hit", client.calls[0]["system"])


if __name__ == "__main__":
    unittest.main()
