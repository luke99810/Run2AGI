"""Deterministic scheduling helpers for the control plane.

The scheduling layer owns lean flow controls such as WIP limits and ready
queue ordering.  It stays in control-plane land: no LLM calls, no harness
imports, and every decision is replayable from `.codentum/` state.
"""

from __future__ import annotations

from .ready_queue import ReadyQueueEntry, build_ready_queue
from .wip_limiter import (
    SchedulingConfig,
    build_scheduling_projection,
    count_packet_states,
    default_scheduling_config,
    load_scheduling_config,
    remaining_capacity,
    under_wip_limit,
)

__all__ = [
    "ReadyQueueEntry",
    "SchedulingConfig",
    "build_ready_queue",
    "build_scheduling_projection",
    "count_packet_states",
    "default_scheduling_config",
    "load_scheduling_config",
    "remaining_capacity",
    "under_wip_limit",
]
