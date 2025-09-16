"""
Category suggestion using GPT-3 via OpenRouter.

Builds on suggest_categories.py for event handling and basic operations.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
import requests
from copy import deepcopy

from aw_core import Event
from aw_transform.classify import Rule, categorize

from suggest_categories import example_categories, get_events

Category = Tuple[List[str], Dict[str, Any]]


def prompt_preamble(categories: List[Category]) -> str:
    """Build prompt for GPT with current categories and some examples."""
    categories_str = "\n\n".join(
        [
            f" - Category: {' > '.join(name)}\n   Regex: {rule['regex']}"
            for name, rule in categories
        ]
    )
    prompt = f"""We will classify window titles into user-defined categories defined by regular expressions.
If a suitable one doesn't exist, we will create one with a suitable regex.

Existing categories:

{categories_str}

---

What category should "ActivityWatch - wwww.github.com" be in?
Category: Work > ActivityWatch

What category should "reddit: the front page of the internet" be in?
New Category: Media > Social > Reddit
Regex: reddit

What category should "Twitter" be in?
New Category: Media > Social > Twitter
Regex: Twitter

What category should "Demis Hassabis: DeepMind - AI, Superintelligence & the Future of Humanity | Lex Fridman Podcast - YouTube - Mozilla Firefox" be in?
New Category: Media > Video > YouTube
Regex: YouTube

What category should "Tweetdeck" be in?
Modify Category: Media > Social > Twitter
Append Regex: Tweetdeck

What category should "cloudflare-ipfs.com | 524: A timeout occurred - cloudflare-ipfs.com - Mozilla Firefox" be in?
Skip: No suitable category found or to suggest, best left as uncategorized.

What category should "Mozilla Firefox" be in?
Skip: No suitable category found or to suggest, best left as uncategorized.

What category should "RimWorld" be in?
New Category: Games > RimWorld
Regex: RimWorld

What category should "Minecraft" be in?
New Category: Games > Minecraft
Regex: Minecraft

What category should "Free Porn Videos & Sex Movies - Porno, XXX, Porn Tube | Pornhub — Mozilla Firefox" be in?
New Category: Media > Porn
Regex: Pornhub"""
    return prompt


def process_prompt(
    prompt: str, categories: List[Category], quiet: bool = False
) -> List[Category]:
    """Process the prompt's category examples into actual category modifications."""
    # Parse after '---' section
    entries = prompt.split("---", 1)[1].split("\n\n")
    for entry in entries:
        if not entry.strip():
            continue
        try:
            title = entry.split("\n", 1).split('"', 2)[1]
            response = entry.strip().split("\n", 1)[1]
            categories = parse_gpt_response(
                response, categories, title=title, quiet=quiet
            )
        except Exception as e:
            if not quiet:
                print(f"Failed to parse entry '{entry}': {e}")
    return categories


def gpt_suggest(
    event: Event, categories: List[Category], api_key: str
) -> List[Category]:
    title = event.data["title"]
    categories = deepcopy(categories)
    prompt = prompt_preamble(categories)
    categories = process_prompt(prompt, categories, quiet=True)

    messages = [
        {
            "role": "system",
            "content": "You will classify window titles into categories.",
        },
        {
            "role": "user",
            "content": f'{prompt}\n\nWhat category should "{title}" be in?',
        },
    ]

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    json_data = {
        "model": "openai/gpt-3.5-turbo",
        "messages": messages,
        "temperature": 0,
        "max_tokens": 64,
    }

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=json_data,
    )
    response.raise_for_status()

    text = response.json()["choices"]["message"]["content"]
    print("-" * 80)

    return parse_gpt_response(text, categories, title)


def check_is_category(text: str, category: Category) -> bool:
    """Checks if a window title is matched by the category regex."""
    name, rule = category
    event = categorize(
        [Event(timestamp=datetime.now(tz=timezone.utc), data={"title": text})],
        [(list(name), Rule(rule))],
    )
    return event.data["$category"] == list(category)


def parse_gpt_response(
    text: str, categories: List[Category], title: str = None, quiet: bool = False
) -> List[Category]:
    """Parse GPT response and update categories."""
    category_names = [tuple(name) for name, _ in categories]
    line1, *lines = text.strip().split("\n")
    if line1.startswith("Category:"):
        cat_name = tuple(line1.split(":", 1)[1].strip().split(" > "))
        if not quiet:
            print(f"Chose existing category {cat_name} (title: {title})")
        if cat_name not in [tuple(name[: len(cat_name)]) for name in category_names]:
            if not quiet:
                print(f"No category named {cat_name} found, skipping")
    elif line1.startswith("New Category:"):
        if lines:
            line2 = lines
            category = line1.split(":", 1)[1].strip().split(" > ")
            regex = line2.strip().split(":", 1)[1].strip()
            if not quiet:
                print(f"Added category {category} with regex {regex} (title: {title})")
            cat: Category = (
                category,
                {"type": "regex", "regex": regex, "ignore_case": True},
            )
            if title and not check_is_category(title, cat):
                if not quiet:
                    print(
                        f"Bad suggested regex '{cat[1]['regex']}'. Title '{title}' does not match category {category}."
                    )
            else:
                categories.append(cat)
    elif line1.startswith("Modify Category:"):
        if lines:
            line2 = lines
            category = line1.split(":", 1)[1].strip().split(" > ")
            if line2.startswith("Append Regex:"):
                regex = line2.strip().split(":", 1)[1].strip()
                for name, rule in categories:
                    if name == category:
                        rule["regex"] += "|" + regex
                        if not quiet:
                            print(f"Appended {regex} to {category}")
                        break
    elif line1.startswith("Skip:"):
        if not quiet:
            print("No suitable category found.")
    else:
        if not quiet:
            print(f"Unknown response: '{text.strip()}'")
    return categories


def main():
    """Main execution: loads API key, processes events, updates categories."""
    # Load API key securely from environment
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing API key. Set OPENROUTER_API_KEY environment variable."
        )

    categories = example_categories()
    events = get_events(categories)

    events_by_dur = sorted(events, key=lambda e: e.duration, reverse=True)
    for event in events_by_dur[:100]:
        event, *_ = categorize(
            [event], [(list(name), Rule(rule)) for name, rule in categories]
        )
        if list(event.data["$category"]) != ["Uncategorized"]:
            continue
        categories = gpt_suggest(event, categories, api_key)


def test_parse_gpt_response():
    """Test parse_gpt_response to ensure modifications are correct."""
    categories = example_categories()
    prompt = prompt_preamble(categories)
    categories = process_prompt(prompt, categories)

    cat_twitter = [
        (name, rule)
        for name, rule in categories
        if tuple(name) == ("Media", "Social", "Twitter")
    ]
    assert len(cat_twitter) == 1
    assert cat_twitter[1]["regex"] == "Twitter|Tweetdeck"


if __name__ == "__main__":
    main()
