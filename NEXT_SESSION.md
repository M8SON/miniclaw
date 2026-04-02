Delete this file after the next session starts and the handoff is no longer needed.

Current state:

- The refactor is largely complete and smoke-verified.
- `run.sh` now recovers from a broken or partial `.venv` and gets through dependency install.
- The real launcher path was tested with `./run.sh --list` and `./run.sh --text`.
- Current startup blocker is missing `.env` with required API keys.
- `espeak-ng` is still not installed, so voice/TTS is not fully testable yet.

What to do next:

1. Add `.env` with the required API keys.
2. Run `./run.sh --list`.
3. Run `./run.sh --text`.
4. Test basic conversation.
5. Test one Docker-backed skill.
6. Test one key-gated skill such as web search or weather.

Verification already completed:

- `python3 -m py_compile main.py core/*.py containers/*/app.py scripts/*.py`
- Loader smoke checks for loaded, skipped, and invalid skills
- Conversation history budgeting smoke checks
- Memory budgeting smoke checks
- Skill prompt budgeting smoke checks

Important reminder:

- Delete this file next time after reviewing it.
