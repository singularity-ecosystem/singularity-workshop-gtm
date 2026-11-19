#!/usr/bin/env python3
"""Update the current event's Luma description.

Reads LUMA_API_KEY and CURRENT_EVENT from the repo .env, then
events/<CURRENT_EVENT>/.env for EVENT_ID (a Luma event id or public slug).
The description is the markdown file marked **current** in
events/<CURRENT_EVENT>/event-descriptions/history.md.

    python3 scripts/update-luma-description.py
    python3 scripts/update-luma-description.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://public-api.luma.com"
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
CURRENT = re.compile(r"\*\*current\*\*")


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def repo_root() -> Path:
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / ".env").is_file() and (candidate / "events").is_dir():
            return candidate
    raise SystemExit("Could not find the repo root (.env next to events/).")


def require(env: dict[str, str], key: str, source: Path) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise SystemExit(f"{source} is missing {key}.")
    return value


def current_description(history: Path) -> Path:
    if not history.is_file():
        raise SystemExit(f"Missing history file: {history}")

    matches: list[Path] = []
    for number, line in enumerate(history.read_text().splitlines(), 1):
        if not CURRENT.search(line):
            continue
        links = [link for link in LINK.findall(line) if not link.startswith("#")]
        if len(links) != 1:
            raise SystemExit(
                f"{history}:{number} is marked **current** but does not link to one file."
            )
        matches.append((history.parent / links[0]).resolve())

    if len(matches) != 1:
        raise SystemExit(
            f"Expected one **current** description in {history}, found {len(matches)}."
        )
    path = matches[0]
    if not path.is_file():
        raise SystemExit(f"Current description does not exist: {path}")
    return path


def request(api_key: str, method: str, path: str, params=None, body=None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None if body is None else json.dumps(body).encode()
    headers = {"x-luma-api-key": api_key, "accept": "application/json"}
    if data is not None:
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace").strip()
        raise SystemExit(f"Luma {method} {path} failed ({error.code}): {detail}") from error


def resolve_event_id(api_key: str, event_id: str) -> tuple[str, str, str]:
    """Return (evt id, name, public url) for an evt- id or a public slug."""
    if event_id.startswith("evt-"):
        event = request(api_key, "GET", "/v1/events/get", {"event_id": event_id})
        name = event.get("name") or event_id
        slug = event.get("slug")
    else:
        lookup = request(api_key, "GET", "/v1/entities/lookup", {"slug": event_id})
        try:
            event = lookup["entity"]["event"]
            event_id = event["id"]
            name = event.get("name") or event_id
            slug = event.get("slug")
        except (KeyError, TypeError) as error:
            raise SystemExit(
                f"Could not resolve a Luma event from EVENT_ID={event_id}."
            ) from error
        # Confirm this key can manage the event before writing.
        request(api_key, "GET", "/v1/events/get", {"event_id": event_id})

    url = f"https://luma.com/{slug}" if slug else event_id
    return event_id, name, url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the event and description, then print them without updating Luma.",
    )
    args = parser.parse_args()

    root = repo_root()
    env_path = root / ".env"
    env = load_env(env_path)
    api_key = require(env, "LUMA_API_KEY", env_path)
    current_event = require(env, "CURRENT_EVENT", env_path)

    event_dir = root / "events" / current_event
    if not event_dir.is_dir():
        raise SystemExit(f"Current event directory does not exist: {event_dir}")

    event_env_path = event_dir / ".env"
    if not event_env_path.is_file():
        raise SystemExit(f"Missing event env file: {event_env_path}")
    event_key = require(load_env(event_env_path), "EVENT_ID", event_env_path)

    description_path = current_description(event_dir / "event-descriptions" / "history.md")
    description = description_path.read_text().rstrip() + "\n"
    event_id, name, url = resolve_event_id(api_key, event_key)

    print(f"Event: {name}")
    print(f"Luma:  {url} ({event_id})")
    print(f"File:  {description_path.relative_to(root)}")

    if args.dry_run:
        print("Dry run: description was not sent.")
        return

    request(
        api_key,
        "POST",
        "/v1/events/update",
        body={"event_id": event_id, "description_md": description},
    )
    print("Updated the Luma event description.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        print(f"Could not reach Luma: {error.reason}", file=sys.stderr)
        sys.exit(1)
