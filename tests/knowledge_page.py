"""Wat ik weet (E-524) read the way the page groups it, for the tests: one list of rows with
their state; what waits, what was derived, what is confirmed or turned down, and per device."""

from typing import Any

Page = dict[str, Any]


def to_confirm(page: Page) -> list[dict[str, Any]]:
    """Te bevestigen, one by one: what waits, not derived from behaviour, not what an entity is."""
    return [
        r
        for r in page["rows"]
        if r["state"] == "open" and r["source"] != "behaviour" and r["field"] != "function"
    ]


def derived(page: Page) -> list[dict[str, Any]]:
    """What waits and was derived from behaviour, without AI (E-203)."""
    return [r for r in page["rows"] if r["state"] == "open" and r["source"] == "behaviour"]


def confirmed(page: Page) -> list[dict[str, Any]]:
    return [r for r in page["rows"] if r["state"] == "yes"]


def rejected(page: Page) -> int:
    return sum(1 for r in page["rows"] if r["state"] == "no")


def by_device(page: Page) -> list[dict[str, Any]]:
    """What each entity is, waiting, per device (E-432): what one 'Klopt, alles' confirms."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in page["rows"]:
        if r["state"] == "open" and r["field"] == "function":
            groups.setdefault(r.get("deviceId") or r["id"], []).append(r)
    return [
        {
            "name": rows[0].get("device") or rows[0]["name"],
            "keys": [r["id"] for r in rows],
            "items": rows,
        }
        for rows in groups.values()
    ]
