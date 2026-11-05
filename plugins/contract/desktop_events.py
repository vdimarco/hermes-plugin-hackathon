"""Push this plugin's state to connected Hermes Desktop windows.

Hermes delivers `plugin.contract.<event>` to every Desktop window attached to this
process (see hermes_cli.plugin_events). Outside the gateway (hermes chat, cron,
tests) nobody is attached and the call is a no-op. Sending never raises.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

PLUGIN_ID = "contract"


def emit(event: str, payload: Dict[str, Any]) -> None:
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
    except Exception:  # not running inside Hermes
        return
    try:
        broadcast_plugin_event(PLUGIN_ID, event, payload)
    except Exception:
        logger.debug("%s: desktop event %s not sent", PLUGIN_ID, event, exc_info=True)
