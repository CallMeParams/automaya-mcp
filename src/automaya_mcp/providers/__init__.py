"""External integrations: AI 3D generation providers and free asset libraries.

All network I/O lives here on the server side (httpx, async). Maya only ever
receives a local file path to import.
"""
from .base import GenJob, Provider3D, ProviderError, http
from .registry import PROVIDERS, get_provider, list_providers

__all__ = ["GenJob", "Provider3D", "ProviderError", "http", "PROVIDERS", "get_provider", "list_providers"]
