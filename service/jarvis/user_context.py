"""Owner context shared by typed chat and voice; never includes credentials."""

from datetime import datetime
import getpass
import os
from pathlib import Path


def timezone_name() -> str:
    zone = os.environ.get("TZ", "").lstrip(":")
    if not zone:
        try:
            zone = str(Path("/etc/localtime").resolve()).split("/zoneinfo/", 1)[1]
        except (OSError, IndexError):
            zone = ""
    return zone


def timezone_city() -> str:
    zone = timezone_name()
    if "/" not in zone or zone.startswith(("Etc/", "SystemV/")):
        return ""
    return zone.rsplit("/", 1)[-1].replace("_", " ")


def owner_facts(config, memory) -> list[dict]:
    # Recall is a small relevance search. Profile facts must survive unrelated questions
    # and newer memories, so seed every owner conversation from the persistent facts.
    facts = memory.query("SELECT id, text FROM facts ORDER BY id DESC")
    profile = []
    try:
        profile.append(f"Local account username: {getpass.getuser()}")
    except (OSError, KeyError):
        pass
    if name := config["ui"].get("user_name", "").strip():
        profile.append(f"User's name: {name}")
    if city := config["web"].get("city", "").strip():
        profile.append(f"Configured home city: {city}")
    if zone := timezone_name():
        profile.append(f"Device timezone: {zone}. Its city is only an approximate location hint, "
                       "not GPS or a confirmed current/home location. Use it with that qualification "
                       "when no explicit location is known.")
    profile.append(f"Current device date and time: {datetime.now().astimezone().isoformat(timespec='minutes')}")
    return [{"id": 0, "text": text} for text in profile] + facts
