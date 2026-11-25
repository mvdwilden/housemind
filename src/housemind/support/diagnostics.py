"""The diagnostics package (E-98): one button, to share when something is wrong.

It holds versions, how much HouseMind knows and does (counts only), house settings without
anything secret, and the last warnings and errors – with every device, room, person and
address replaced by a neutral label. No names, no history of the house, no keys (E-15, E-86).
"""

from __future__ import annotations

import contextlib
import logging
import platform
import re
from collections import deque
from typing import Any

ENTITY = re.compile(r"\b([a-z_]+)\.([a-z0-9_]+)\b")
IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
URL = re.compile(r"https?://\S+")
KEEP = {"housemind", "asyncio", "aiohttp", "logging", "json", "yaml"}  # module names


class RecentLog(logging.Handler):
    """Keeps the last warnings and errors in memory (never written anywhere else)."""

    def __init__(self, size: int = 300) -> None:
        super().__init__(level=logging.WARNING)
        self.records: deque[str] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):  # a log handler must never fail
            self.records.append(self.format(record))


RECENT = RecentLog()
RECENT.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))


def anonymise(text: str, names: list[str]) -> str:
    """Replace device ids, names, addresses and links by neutral labels."""
    labels: dict[str, str] = {}

    def entity(match: re.Match[str]) -> str:
        domain, rest = match.group(1), match.group(0)
        if domain in KEEP or domain.startswith("housemind"):
            return rest
        if rest not in labels:
            labels[rest] = f"{domain}.device_{len(labels) + 1}"
        return labels[rest]

    text = URL.sub("<link>", text)
    text = EMAIL.sub("<email>", text)
    text = IP.sub("<address>", text)
    for name in sorted((n for n in names if len(n) >= 3), key=len, reverse=True):
        text = re.sub(re.escape(name), "<name>", text, flags=re.IGNORECASE)
    return ENTITY.sub(entity, text)


def package(facts: dict[str, Any], names: list[str]) -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        **facts,
        "log": [anonymise(line, names) for line in RECENT.records],
    }
