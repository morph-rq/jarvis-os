"""WhatsApp as a messaging provider: only what really exists, which today is nothing to read.

Personal WhatsApp is linked through Hermes's Baileys bridge in pair-only mode (accounts/connectors/
whatsapp.py): the session exists, but no part of Jarvis reads chats through it, so this provider
declares no capabilities and says why. WhatsApp Business (Cloud API) receives only messages people
send to the business number, by webhook, into Jarvis's gateway (talking to Jarvis), never the user's
personal inbox; it is not an inbox provider either.
"""

from __future__ import annotations

from jarvis.comms.provider import MessagingProvider


class WhatsAppPersonalProvider(MessagingProvider):
    provider = "whatsapp"
    label = "WhatsApp"
    capabilities = frozenset()
    why_limited = ("Personal WhatsApp is linked for pairing only: Jarvis has no WhatsApp reader yet, so it can't "
                   "list, read, search or send your WhatsApp chats. Open WhatsApp itself for those.")


class WhatsAppBusinessProvider(MessagingProvider):
    provider = "whatsapp_business"
    label = "WhatsApp Business"
    capabilities = frozenset()
    why_limited = ("WhatsApp Business (Cloud API) only delivers messages sent to the business number, to Jarvis's "
                   "messaging gateway by webhook; it has no access to a personal inbox.")
