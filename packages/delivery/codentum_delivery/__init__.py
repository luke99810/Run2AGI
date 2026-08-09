"""Role-C delivery boundary for sidecar transport, packaging, and release gates.

The package is model-free and does not write control-plane state.  Secret scanning is
non-bypassable; its final QA-first acceptance remains owned by role A.
"""

from .gateway import SidecarGateway

__all__ = ["SidecarGateway"]
