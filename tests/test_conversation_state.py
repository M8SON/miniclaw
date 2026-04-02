import unittest

from core.conversation_state import ConversationState


class FakeBlock:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return dict(self._payload)


class ConversationStateTests(unittest.TestCase):
    def test_append_assistant_content_normalizes_sdk_blocks(self):
        state = ConversationState()

        state.append_assistant_content([FakeBlock({"type": "text", "text": "hello"})])

        self.assertEqual(
            state.messages,
            [{"role": "assistant", "content": [{"type": "text", "text": "hello"}]}],
        )

    def test_prune_keeps_recent_whole_turns(self):
        state = ConversationState(max_messages=4, max_tokens=10_000)
        state.append_user_text("turn one")
        state.append_assistant_content([{"type": "text", "text": "one"}])
        state.append_user_text("turn two")
        state.append_assistant_content([{"type": "text", "text": "two"}])
        state.append_user_text("turn three")
        state.append_assistant_content([{"type": "text", "text": "three"}])

        state.prune()

        self.assertEqual(
            state.messages,
            [
                {"role": "user", "content": "turn two"},
                {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
                {"role": "user", "content": "turn three"},
                {"role": "assistant", "content": [{"type": "text", "text": "three"}]},
            ],
        )


if __name__ == "__main__":
    unittest.main()
