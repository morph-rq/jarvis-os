"""Provider adapters. Each is optional and reports what it needs when it is not set up."""

from jarvis.integrations.providers.composio import ComposioProvider
from jarvis.integrations.providers.genoffice import GenOfficeProvider
from jarvis.integrations.providers.google import GoogleProvider

ALL = (GoogleProvider, ComposioProvider, GenOfficeProvider)
