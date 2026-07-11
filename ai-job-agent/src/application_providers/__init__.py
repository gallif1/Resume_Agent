"""Job application provider adapters."""

from application_providers.registry import PROVIDER_CLASSES, select_provider

__all__ = ["PROVIDER_CLASSES", "select_provider"]
