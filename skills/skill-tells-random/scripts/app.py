"""
Random joke skill container - fetches a random joke from a public API.
"""

import random
import requests


JOKE_APIS = [
    "https://official-joke-api.appspot.com/random_joke",
    "https://v2.jokeapi.dev/joke/Any?blacklistFlags=nsfw,racist,sexist",
]


def fetch_joke() -> str:
    apis = JOKE_APIS[:]
    random.shuffle(apis)

    for url in apis:
        try:
            response = requests.get(url, timeout=8)
            if response.status_code != 200:
                continue

            data = response.json()

            # Official Joke API format: {"setup": "...", "punchline": "..."}
            if "setup" in data and "punchline" in data:
                return f"{data['setup']} ... {data['punchline']}"

            # JokeAPI twopart format
            if data.get("type") == "twopart":
                return f"{data['setup']} ... {data['delivery']}"

            # JokeAPI single format
            if data.get("type") == "single" and "joke" in data:
                return data["joke"]

        except Exception:
            continue

    return "Why don't scientists trust atoms? Because they make up everything."


def main():
    print(fetch_joke())


if __name__ == "__main__":
    main()
