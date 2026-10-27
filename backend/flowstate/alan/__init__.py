"""Typed adapter for invoking FLOWSTATE from Alan.

The adapter is a narrow boundary: it maps a fixed set of natural-language
intents to typed backend operations, validates arguments, requires a two-step
confirmation token for consequential actions, and refuses to claim success
without a persisted SUCCEEDED/VERIFIED result.
"""

from flowstate.alan.client import AdapterError, FlowstateClient
from flowstate.alan.service import AlanOperations

__all__ = ["AdapterError", "AlanOperations", "FlowstateClient"]
