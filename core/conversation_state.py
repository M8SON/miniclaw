"""
Conversation state for MiniClaw.

Owns the mutable message history sent to Claude during a session.
"""

import json


class ConversationState:
    """Mutable conversation history with helpers for common message types."""

    def __init__(
        self,
        max_messages: int | None = 24,
        max_tokens: int | None = 6000,
    ):
        self._messages: list[dict] = []
        self.max_messages = max_messages
        self.max_tokens = max_tokens

    @property
    def messages(self) -> list[dict]:
        """Return the full conversation history."""
        return self._messages

    def append_user_text(self, text: str) -> None:
        """Append a plain user text turn."""
        self._messages.append({"role": "user", "content": text})

    def append_assistant_content(self, content: list[dict]) -> None:
        """Append the assistant response blocks returned by Anthropic."""
        self._messages.append({"role": "assistant", "content": content})

    def append_tool_results(self, tool_results: list[dict]) -> None:
        """Append tool results as the next user turn in Anthropic format."""
        self._messages.append({"role": "user", "content": tool_results})

    def prune(self) -> None:
        """
        Keep only the most recent completed conversation turns.

        Pruning is intended to happen at safe boundaries between user turns so
        the active tool loop does not lose context mid-request.
        """
        self._messages = self.select_messages_for_prompt(self._messages)

    def select_messages_for_prompt(self, messages: list[dict] | None = None) -> list[dict]:
        """Return the newest whole turns that fit within message and token budgets."""
        source = messages if messages is not None else self._messages
        if not source:
            return []

        turns = self._split_turns(source)
        if not turns:
            return list(source)

        retained_turns = []
        retained_message_count = 0
        retained_token_count = 0

        for turn in reversed(turns):
            turn_message_count = len(turn)
            turn_token_count = self._estimate_turn_tokens(turn)

            exceeds_message_budget = self._would_exceed_budget(
                current=retained_message_count,
                addition=turn_message_count,
                limit=self.max_messages,
            )
            exceeds_token_budget = self._would_exceed_budget(
                current=retained_token_count,
                addition=turn_token_count,
                limit=self.max_tokens,
            )

            if retained_turns and (exceeds_message_budget or exceeds_token_budget):
                break

            retained_turns.append(turn)
            retained_message_count += turn_message_count
            retained_token_count += turn_token_count

        retained_turns.reverse()
        return [message for turn in retained_turns for message in turn]

    def clear(self) -> None:
        """Reset the conversation history."""
        self._messages = []

    def _would_exceed_budget(self, current: int, addition: int, limit: int | None) -> bool:
        """Return True if adding a turn would exceed the configured budget."""
        if limit is None or limit <= 0:
            return False
        return current + addition > limit

    def _is_user_text_message(self, message: dict) -> bool:
        """Return True for a normal user utterance and False for tool results."""
        return message.get("role") == "user" and isinstance(message.get("content"), str)

    def _estimate_turn_tokens(self, turn: list[dict]) -> int:
        """Approximate token count for a full turn using serialized message size."""
        serialized = json.dumps(turn, separators=(",", ":"), ensure_ascii=False)
        return max(1, len(serialized) // 4)

    def _split_turns(self, messages: list[dict]) -> list[list[dict]]:
        """
        Split message history into conversational turns.

        A turn starts with a plain user utterance and includes all following
        assistant/tool-result messages until the next plain user utterance.
        """
        turns = []
        current_turn = []

        for message in messages:
            if self._is_user_text_message(message) and current_turn:
                turns.append(current_turn)
                current_turn = [message]
            else:
                current_turn.append(message)

        if current_turn:
            turns.append(current_turn)

        return turns
