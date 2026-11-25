"""HouseMind as a conversation agent for Assist (E-167 – E-170).

With "prefer handling commands locally" on, HA does direct commands itself; HouseMind answers
questions about the house, takes house rules by voice (read back, then yes or no) and only
forwards other questions if the administrator allowed it in the App.
"""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.ulid import ulid_now

from . import HouseMindConfigEntry
from .api import HouseMindError
from .entity import HouseMindEntity

UNREACHABLE = {
    "nl": "HouseMind is nu niet bereikbaar.",
    "de": "HouseMind ist gerade nicht erreichbar.",
    "fr": "HouseMind n'est pas joignable pour le moment.",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HouseMindConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HouseMindAgent(entry)])


class HouseMindAgent(HouseMindEntity, conversation.ConversationEntity):
    _attr_name = None  # the device's name: HouseMind, or the brain's own name (E-170)

    def __init__(self, entry: HouseMindConfigEntry) -> None:
        super().__init__(entry.runtime_data.coordinator, "agent")
        self._client = entry.runtime_data.client

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        conversation_id = user_input.conversation_id or ulid_now()
        language = user_input.language or self.hass.config.language
        try:
            reply = await self._client.converse(
                user_input.text, language, conversation_id, user_input.context.user_id
            )
            text, keep_going = str(reply.get("text") or ""), bool(reply.get("continue"))
        except HouseMindError:
            text = UNREACHABLE.get(language[:2], "HouseMind cannot be reached right now.")
            keep_going = False
        response = intent.IntentResponse(language=language)
        response.async_set_speech(text)
        return conversation.ConversationResult(
            response=response,
            conversation_id=conversation_id,
            continue_conversation=keep_going,
        )
