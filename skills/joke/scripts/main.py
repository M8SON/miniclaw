import os, sys, json, random

raw = os.environ.get("SKILL_INPUT", "") or sys.stdin.read()
try:
    data = json.loads(raw)
    topic = data.get("topic", "").lower()
except Exception:
    topic = ""

jokes = {
    "programming": [
        "Why do programmers prefer dark mode? Because light attracts bugs.",
        "A SQL query walks into a bar, walks up to two tables and asks: 'Can I join you?'",
        "Why did the programmer quit his job? Because he didn't get arrays.",
    ],
    "": [
        "I told my wife she was drawing her eyebrows too high. She looked surprised.",
        "Why can't you explain puns to kleptomaniacs? They always take things literally.",
        "I used to hate facial hair, but then it grew on me.",
    ],
}

pool = jokes.get(topic, jokes[""])
print(random.choice(pool))
