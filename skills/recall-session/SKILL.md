---
name: recall-session
description: Search past conversation transcripts. Use when the user references something
  said in a prior session.
---
Search the local archive of past conversations for content matching a query.

When to use:
- User references a prior conversation ("what did we decide about X last week?")
- User asks "did we ever talk about X?" or "when did we last discuss X?"
- You need to verify what was actually said in a past session

When NOT to use:
- For general facts or preferences — those live in saved memory and are already in your prompt
- For the current ongoing conversation — that is in your context window

Input (JSON via SKILL_INPUT):
- query (required): keywords or short phrase to search for
- since (optional): ISO date "2026-04-15" or relative "yesterday" / "last week" / "N days ago"
- limit (optional): max results, default 5

Output: dated snippets ordered by relevance, each with surrounding turns for context.
Tell the user when matches were from and quote the relevant lines. If nothing matches, say so plainly.
