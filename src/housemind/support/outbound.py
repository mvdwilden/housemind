"""Everything that leaves the house, in one list (E-14, E-15, 20.19).

Six flows: the AI, knowledge updates, notifications on a phone, speech, forwarding a question
to a conversation agent, and the diagnostics package; the anonymous count is gone (E-442).
For each: what goes, when, what turns it off, which code sends it, and the word the privacy
statement uses for it in every language. ``tests/test_outbound.py`` keeps the code, this list
and the privacy statement the same: a new way out without a flow here, or a flow the
statement does not name, fails the test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Flow:
    key: str
    what: str
    when: str
    off: str  # what turns it off
    code: tuple[str, ...]  # where it is sent: a module, a class or a function in it
    privacy: dict[str, str]  # a word the privacy statement uses for it, per language


FLOWS = (
    Flow(
        "ai",
        "a short question with neutral names, never people's names, address or recordings",
        "only after an administrator turned the AI on and said yes",
        "House settings › Vesta › AI",
        ("housemind.ai.providers",),
        {"nl": "AI", "en": "AI", "de": "KI", "fr": "IA"},
    ),
    Flow(
        "updates",
        "a request for new knowledge files (device kinds, countries, languages); nothing about "
        "the house",
        "at most once a day",
        "House settings › Upkeep and privacy › knowledge updates",
        ("housemind.knowledge.updates",),
        {
            "nl": "Kennisupdates",
            "en": "Knowledge updates",
            "de": "Wissensupdates",
            "fr": "Mises à jour des connaissances",
        },
    ),
    Flow(
        "push",
        "the text of a notification, through Home Assistant's notifications to a phone",
        "when a notification goes to someone's phone",
        "My preferences › About me › Notifications",
        (
            "housemind.brain.notify.NotificationRouter",
            "housemind.runtime.HouseMind._tell_safe_mode",  # after a failed update (E-301)
        ),
        {
            "nl": "Meldingen op je telefoon",
            "en": "Messages on your phone",
            "de": "Meldungen auf deinem Telefon",
            "fr": "messages sur ton téléphone",
        },
    ),
    Flow(
        "speech",
        "what Vesta says and what you say to her, through Home Assistant's speech pipeline; "
        "with a cloud engine there, it goes to that service",
        "when Vesta speaks or listens",
        "House settings › Vesta (her voice and pipeline)",
        (
            "housemind.brain.speech.Speech",
            "housemind.parts.vesta.VestaPart.hear",
            "housemind.parts.vesta.VestaPart.vesta_audio",
        ),
        {"nl": "Spraak", "en": "Speech", "de": "Sprache", "fr": "Parole"},
    ),
    Flow(
        "forward",
        "a question HouseMind cannot answer itself, to the agent the administrator chose",
        "only when forwarding is on",
        "House settings › Vesta › Assist",
        ("housemind.ha.integration.IntegrationLink._forward",),
        {"nl": "Doorsturen", "en": "Forwarding", "de": "Weiterleiten", "fr": "Transmettre"},
    ),
    Flow(
        "diagnostics",
        "the diagnostics package, with labels instead of names",
        "only when someone shares the file themselves",
        "it is never sent by itself",
        ("housemind.support.diagnostics",),
        {
            "nl": "diagnosepakket",
            "en": "diagnostics package",
            "de": "Diagnosepaket",
            "fr": "paquet de diagnostic",
        },
    ),
)

# Connections that stay in the house: Home Assistant and its Supervisor.
INSIDE = ("housemind.ha.websocket", "housemind.ha.supervisor")
