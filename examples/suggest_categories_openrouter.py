"""
Uses GPT-3 to suggest new categories.

Builds on suggest_categories.py for basic operations, like getting events.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
import requests
from copy import deepcopy

from aw_core import Event
from aw_transform.classify import Rule, categorize

from suggest_categories import example_categories, get_events

Category = Tuple[List[str], Dict[str, Any]]


def prompt_preamble(categories: List[Category]) -> str:
    categories_str = "\n\n".join(
        [
            f" - Category: {' > '.join(name)}\n   Regex: {rule['regex']}"
            for name, rule in categories
        ]
    )

    prompt = f"""We will classify window titles into user-defined categories defined by regular expressions.
If a suitable one doesn't exists, we will create one with a suitable regex.

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


def process_prompt(prompt, categories, quiet=False) -> List[Category]:
    """processes the prompt preamble for categories created/modified in the prompt"""
    for entry in prompt.split("---", 1)[1].split("\n\n"):
        if not entry.strip():
            continue
        # FIXME: Will break if string contains double-quotes
        title = entry.split("\n", 1)[0].split('"', 2)[1]
        response = entry.strip().split("\n", 1)[1]
        categories = parse_gpt_response(response, categories, title=title, quiet=quiet)
    return categories


def gpt_suggest(
    event: Event, categories: List[Category], api_key: str
) -> List[Category]:
    title = event.data["title"]
    categories = deepcopy(categories)

    prompt = prompt_preamble(categories)
    categories = process_prompt(prompt, categories, quiet=True)

    # Use chat message format
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
        "model": "openai/gpt-3.5-turbo",  # Or other model you have access to on OpenRouter
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

    text = response.json()["choices"][0]["message"]["content"]
    print("-" * 80)

    return parse_gpt_response(text, categories, title)


def check_is_category(text: str, category: Category) -> bool:
    name, rule = category
    event = categorize(
        [Event(timestamp=datetime.now(tz=timezone.utc), data={"title": text})],
        [(list(name), Rule(rule))],
    )[0]
    return event.data["$category"] == list(category[0])


def parse_gpt_response(text: str, categories: List[Category], title=None, quiet=False):
    category_names = [tuple(name) for name, _ in categories]

    line1, *lines = text.strip().split("\n")
    if line1.startswith("Category:"):
        # chose existing category
        cat_name = tuple(line1.split(":", 1)[1].strip().split(" > "))
        if not quiet:
            print(f"Chose existing category {cat_name}  (title: {title})")
        if cat_name not in [tuple(name[: len(cat_name)]) for name in category_names]:
            print(f"No category named {cat_name} found, skipping")
    elif line1.startswith("New Category:"):
        line2 = lines[0]
        category = line1.split(":", 1)[1].strip().split(" > ")
        regex = line2.strip().split(":", 1)[1].strip()
        if not quiet:
            print(f"Added category {category} with regex {regex}  (title: {title})")
        cat: Category = (
            category,
            {"type": "regex", "regex": regex, "ignore_case": True},
        )
        if title and not check_is_category(title, cat):
            print(
                f"Bad suggested regex '{cat[1]['regex']}'. Title '{title}' does not match category {category}."
            )
        else:
            categories.append(cat)
    elif line1.startswith("Modify Category:"):
        line2 = lines[0]
        category = line1.split(":", 1)[1].strip().split(" > ")
        assert line2.startswith("Append Regex:")
        regex = line2.strip().split(":", 1)[1].strip()
        # get existing category
        for name, rule in categories:
            if name == category:
                rule["regex"] += "|" + regex
                if not quiet:
                    print(f"Appended {regex} to {category}")
                break
    elif line1.startswith("Skip:"):
        pass
    else:
        print(f"Unknown response: '{text.strip()}'")

    return categories


def main():
    categories = example_categories()
    events = get_events(categories)

    api_key = "YOUR_OPENROUTER_API_KEY"  # Replace this with your actual OpenRouter API key

    events_by_dur = sorted(events, key=lambda e: e.duration, reverse=True)
    for event in events_by_dur[:100]:
        # re-categorize and skip event if it is already categorized by a new rule
        event, *_ = categorize(
            [event], [(list(name), Rule(rule)) for name, rule in categories]
        )
        if list(event.data["$category"]) != ["Uncategorized"]:
            continue

        categories = gpt_suggest(event, categories, api_key)


def test_parse_gpt_response():
    categories = example_categories()
    prompt = prompt_preamble(categories)
    categories = process_prompt(prompt, categories)

    cat_twitter = list(
        (name, rule)
        for name, rule in categories
        if tuple(name) == ("Media", "Social", "Twitter")
    )
    assert len(cat_twitter) == 1
    assert cat_twitter[0][1]["regex"] == "Twitter|Tweetdeck"


if __name__ == "__main__":
    main()
