"""Public, local-only plugin contracts and registry helpers."""

from restore_rehearsal.plugins.model import (
    PLUGIN_API_VERSION,
    AnalysisContext,
    AnalyzerPlugin,
    ArtifactImporter,
    ImportBundle,
    ImportContext,
    PluginDescriptor,
    RestoreProvider,
)
from restore_rehearsal.plugins.registry import (
    PluginRegistry,
    builtin_registry,
    plugin_catalog,
    plugin_doctor,
)

__all__ = [
    "PLUGIN_API_VERSION",
    "AnalysisContext",
    "AnalyzerPlugin",
    "ArtifactImporter",
    "ImportBundle",
    "ImportContext",
    "PluginDescriptor",
    "PluginRegistry",
    "RestoreProvider",
    "builtin_registry",
    "plugin_catalog",
    "plugin_doctor",
]
