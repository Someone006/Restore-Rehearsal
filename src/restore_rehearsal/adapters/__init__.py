"""Offline recovery-adapter catalog and bounded artifact detection."""

from restore_rehearsal.adapters.registry import (
    AdapterProposal,
    RecoveryAdapter,
    adapter_catalog,
    detect_adapters,
    get_adapter,
    propose_adapter,
)

__all__ = [
    "AdapterProposal",
    "RecoveryAdapter",
    "adapter_catalog",
    "detect_adapters",
    "get_adapter",
    "propose_adapter",
]
