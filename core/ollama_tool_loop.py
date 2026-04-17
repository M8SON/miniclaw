"""
OllamaToolLoop - Ollama-backed tool loop for MiniClaw.

Mirrors ToolLoop's interface but calls Ollama's OpenAI-compatible API
(/v1/chat/completions with tools parameter).

Returns EscalateSignal when it cannot handle the request — the Orchestrator
then re-runs the same turn with Claude's ToolLoop. ConversationState is NOT
updated until the loop succeeds, so Claude's ToolLoop can append the user
message and full exchange itself.
"""

import json
import logging
import re

import requests

logger = logging.getLogger(__name__)


class _EscalateSignalType:
    """Singleton sentinel returned when Ollama cannot handle a request."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "EscalateSignal"


# Module-level singleton — use `result is EscalateSignal` to detect escalation.
EscalateSignal = _EscalateSignalType()

_REMEMBER_RE = re.compile(
    r"\n?##\s*remember:\n+topic:\s*(.+?)\n+content:\s*(.+?)(?=\n##|\Z)",
    re.IGNORECASE | re.DOTALL,
)


class OllamaToolLoop:
    """
    Execute an Ollama tool-use loop for a single user message.

    Uses Ollama's OpenAI-compatible API. Tool results are passed back as
    OpenAI tool messages. ConversationState is only updated on success so
    that escalation to Claude leaves a clean slate.
    """

    ESCALATE_WORD = "ESCALATE"

    def __init__(
        self,
        host: str,
        model: str,
        skill_loader,
        container_manager,
        conversation_state,
        memory_provider=None,
        timeout_seconds: float = 8.0,
        max_rounds: int = 10,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.skill_loader = skill_loader
        self.container_manager = container_manager
        self.conversation_state = conversation_state
        self.memory_provider = memory_provider
        self.timeout = timeout_seconds
        self.max_rounds = max_rounds

    def run(self, user_message: str, system_prompt: str) -> "str | _EscalateSignalType":
        """
        Process a user message through Ollama with tool support.

        Returns a string response on success, or EscalateSignal if Ollama
        cannot handle the request. ConversationState is only modified on success.
        """
        local_messages = self._build_local_messages(system_prompt, user_message)
        tool_definitions = self._build_tool_definitions()
        rounds = 0

        while rounds < self.max_rounds:
            rounds += 1

            try:
                response = requests.post(
                    f"{self.host}/v1/chat/completions",
                    json={
                        "model": self.model,
                        "messages": local_messages,
                        "tools": tool_definitions or None,
                        "stream": False,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except requests.Timeout:
                logger.warning("OllamaToolLoop: timeout after %.1fs → escalate", self.timeout)
                return EscalateSignal
            except requests.RequestException as exc:
                logger.warning("OllamaToolLoop: request error %s → escalate", exc)
                return EscalateSignal

            # Tool call handling and response extraction added in Task 5
            try:
                data = response.json()
                choice = data["choices"][0]
                message = choice["message"]
            except (ValueError, KeyError, IndexError) as exc:
                logger.warning("OllamaToolLoop: unexpected response format %s → escalate", exc)
                return EscalateSignal

            content = message.get("content") or ""
            finish_reason = choice.get("finish_reason", "stop")

            # Explicit ESCALATE signal from model
            if content.strip().upper() == self.ESCALATE_WORD:
                logger.info("OllamaToolLoop: model signalled ESCALATE → escalate")
                return EscalateSignal

            # Tool calls
            if finish_reason == "tool_calls" and message.get("tool_calls"):
                tool_calls = message["tool_calls"]
                local_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                })

                for tc in tool_calls:
                    tool_name = tc["function"]["name"]
                    skill = self.skill_loader.get_skill(tool_name)
                    if not skill:
                        logger.warning(
                            "OllamaToolLoop: unknown tool %r → escalate", tool_name
                        )
                        return EscalateSignal

                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except (json.JSONDecodeError, KeyError):
                        logger.warning(
                            "OllamaToolLoop: malformed args for %r → escalate", tool_name
                        )
                        return EscalateSignal

                    try:
                        result = self.container_manager.execute_skill(skill, args)
                    except Exception as exc:
                        logger.warning("OllamaToolLoop: tool %s raised %s → escalate", tool_name, exc)
                        return EscalateSignal

                    if result is None:
                        logger.warning("OllamaToolLoop: tool %s returned None → escalate", tool_name)
                        return EscalateSignal

                    result = self._extract_and_save_remember(result)
                    logger.info("OllamaToolLoop: tool %s → %s", tool_name, result[:100])

                    local_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
                continue

            # Check for finish_reason mismatch with tool_calls
            if message.get("tool_calls") and finish_reason != "tool_calls":
                logger.warning(
                    "OllamaToolLoop: message has tool_calls but finish_reason=%r — "
                    "model may need tool_call_id support; escalating",
                    finish_reason,
                )
                return EscalateSignal

            # Final text response
            if content:
                self._commit_to_state(user_message, content)
                logger.info("OllamaToolLoop: response ready in %d round(s)", rounds)
                return content

            logger.warning("OllamaToolLoop: empty response → escalate")
            return EscalateSignal

        logger.warning("OllamaToolLoop: max rounds (%d) reached → escalate", self.max_rounds)
        return EscalateSignal

    def _build_local_messages(self, system_prompt: str, user_message: str) -> list[dict]:
        """
        Build an OpenAI-format message list from ConversationState history.

        Only plain text user/assistant turns are included — Anthropic tool
        blocks are skipped since Ollama uses a different tool format.
        """
        local = [{"role": "system", "content": system_prompt}]
        history = []
        for msg in self.conversation_state.select_messages_for_prompt():
            role = msg.get("role")
            content = msg.get("content")
            if role == "user" and isinstance(content, str):
                history.append({"role": "user", "content": content})
            elif role == "assistant" and isinstance(content, list):
                text = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
                if text:
                    history.append({"role": "assistant", "content": text})
        # Strip leading assistant messages — OpenAI protocol requires user turn first.
        # This can happen if pruning drops the user turn that preceded a tool exchange.
        while history and history[0]["role"] == "assistant":
            history.pop(0)
        local.extend(history)
        local.append({"role": "user", "content": user_message})
        return local

    def _build_tool_definitions(self) -> list:
        """Convert Anthropic-format tool definitions to OpenAI format."""
        result = []
        for td in self.skill_loader.get_tool_definitions():
            result.append({
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td.get("description", ""),
                    "parameters": td.get("input_schema", {}),
                },
            })
        return result

    def _commit_to_state(self, user_message: str, assistant_response: str) -> None:
        """Commit a successful turn to ConversationState."""
        self.conversation_state.append_user_text(user_message)
        self.conversation_state.append_assistant_content(
            [{"type": "text", "text": assistant_response}]
        )
        self.conversation_state.prune()

    def _extract_and_save_remember(self, result: str) -> str:
        """Strip ## remember: blocks from skill output and file them to the memory vault."""
        if not self.memory_provider or "## remember:" not in result.lower():
            return result
        cleaned = result
        for match in _REMEMBER_RE.finditer(result):
            topic = match.group(1).strip()
            content = match.group(2).strip()
            if topic and content:
                filename = self.memory_provider.save_note(topic, content)
                if filename:
                    logger.info("OllamaToolLoop: skill filed memory: %s", filename)
            cleaned = cleaned.replace(match.group(0), "")
        return cleaned.strip() or "Skill completed with no output"
