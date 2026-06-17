"""Generic HTTP client utilities.

Domain-specific HTTP clients (e.g. system workflow endpoints) are provided
by the domain app in its own http_client module.
"""

import httpx

from mltek.config import config
from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)

