"""Explicit plugin registry; third-party entry points are never loaded automatically."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any

from restore_rehearsal.errors import SafetyError
from restore_rehearsal.plugins.model import (
    PLUGIN_API_VERSION,
    AnalyzerPlugin,
    ArtifactImporter,
    PluginDescriptor,
    RestoreProvider,
)

ENTRY_POINT_GROUPS = {
    "analyzer": "rehearse.analyzers",
    "importer": "rehearse.importers",
    "restore-provider": "rehearse.restore_providers",
}
PLUGIN_ID_PATTERN = re.compile(r"[a-z][a-z0-9.-]{0,99}")


@dataclass
class PluginRegistry:
    _importers: dict[str, ArtifactImporter] = field(default_factory=dict)
    _analyzers: dict[str, AnalyzerPlugin] = field(default_factory=dict)
    _providers: dict[str, RestoreProvider] = field(default_factory=dict)

    def register_importer(self, plugin: ArtifactImporter) -> None:
        self._register(self._importers, plugin, "importer")

    def register_analyzer(self, plugin: AnalyzerPlugin) -> None:
        self._register(self._analyzers, plugin, "analyzer")

    def register_restore_provider(self, plugin: RestoreProvider) -> None:
        self._register(self._providers, plugin, "restore-provider")

    def importer(self, plugin_id: str) -> ArtifactImporter:
        try:
            return self._importers[plugin_id]
        except KeyError as exc:
            raise SafetyError("unknown artifact importer") from exc

    def importers(self) -> tuple[ArtifactImporter, ...]:
        return tuple(self._importers[key] for key in sorted(self._importers))

    def analyzers(self) -> tuple[AnalyzerPlugin, ...]:
        return tuple(self._analyzers[key] for key in sorted(self._analyzers))

    def restore_providers(self) -> tuple[RestoreProvider, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))

    def descriptors(self) -> tuple[PluginDescriptor, ...]:
        result: list[PluginDescriptor] = []
        for kind, plugins in (
            ("analyzer", self._analyzers),
            ("importer", self._importers),
            ("restore-provider", self._providers),
        ):
            for plugin_id, plugin in sorted(plugins.items()):
                result.append(
                    PluginDescriptor(
                        id=plugin_id,
                        kind=kind,
                        version=plugin.version,
                        description=plugin.description,
                        api_version=getattr(plugin, "api_version", PLUGIN_API_VERSION),
                        source="builtin",
                        enabled=True,
                    )
                )
        return tuple(result)

    @staticmethod
    def external_descriptors() -> tuple[PluginDescriptor, ...]:
        """List installed entry-point metadata without importing or executing plugin modules."""

        discovered: list[PluginDescriptor] = []
        entry_points = metadata.entry_points()
        for kind, group in ENTRY_POINT_GROUPS.items():
            selected = entry_points.select(group=group)
            for entry_point in selected:
                if not isinstance(entry_point.name, str) or not PLUGIN_ID_PATTERN.fullmatch(
                    entry_point.name
                ):
                    continue
                distribution_name = _external_metadata_text(
                    entry_point.dist.name if entry_point.dist is not None else None,
                    maximum=200,
                    fallback="external",
                )
                version = _external_metadata_text(
                    entry_point.dist.version if entry_point.dist is not None else None,
                    maximum=32,
                    fallback="unknown",
                )
                discovered.append(
                    PluginDescriptor(
                        id=entry_point.name,
                        kind=kind,
                        version=version,
                        description=f"Installed entry point from {distribution_name}",
                        api_version="unknown",
                        source="external",
                        enabled=False,
                    )
                )
        return tuple(sorted(discovered, key=lambda item: (item.kind, item.id)))

    def activate_external(self, kind: str, plugin_id: str) -> PluginDescriptor:
        """Explicitly import one trusted external entry point.

        Calling this executes third-party Python with the current user's privileges. Nothing in
        Rehearse invokes this method during scans, initialization, or automatic detection.
        """

        group = ENTRY_POINT_GROUPS.get(kind)
        if group is None:
            raise SafetyError("unknown external plugin kind")
        if not isinstance(plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
            raise SafetyError("external plugin identifier is invalid")
        matches = [
            item for item in metadata.entry_points().select(group=group) if item.name == plugin_id
        ]
        if len(matches) != 1:
            raise SafetyError("external plugin entry point was not found or is ambiguous")
        entry_point = matches[0]
        try:
            loaded = entry_point.load()
            plugin = loaded() if isinstance(loaded, type) else loaded
        except BaseException as exc:
            raise SafetyError("external plugin failed during explicit activation") from exc
        if getattr(plugin, "id", None) != plugin_id:
            raise SafetyError("external plugin identifier does not match its entry point")
        if kind == "importer":
            self.register_importer(plugin)
        elif kind == "analyzer":
            self.register_analyzer(plugin)
        else:
            self.register_restore_provider(plugin)
        return PluginDescriptor(
            id=plugin_id,
            kind=kind,
            version=plugin.version,
            description=plugin.description,
            api_version=getattr(plugin, "api_version", PLUGIN_API_VERSION),
            source=_external_metadata_text(
                entry_point.dist.name if entry_point.dist is not None else None,
                maximum=200,
                fallback="external",
            ),
            enabled=True,
        )

    @staticmethod
    def _register(target: dict[str, Any], plugin: Any, kind: str) -> None:
        plugin_id = getattr(plugin, "id", None)
        version = getattr(plugin, "version", None)
        description = getattr(plugin, "description", None)
        api_version = getattr(plugin, "api_version", PLUGIN_API_VERSION)
        if not isinstance(plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
            raise SafetyError(f"{kind} identifier is invalid")
        if not _valid_plugin_text(version, maximum=32):
            raise SafetyError(f"{kind} version is invalid")
        if not _valid_plugin_text(description, maximum=300):
            raise SafetyError(f"{kind} description is invalid")
        if api_version != PLUGIN_API_VERSION:
            raise SafetyError(f"{kind} uses an incompatible plugin API version")
        if plugin_id in target:
            raise SafetyError(f"duplicate {kind} identifier")
        target[plugin_id] = plugin


def _valid_plugin_text(value: Any, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and not any(unicodedata.category(character).startswith("C") for character in value)
    )


def _external_metadata_text(value: Any, *, maximum: int, fallback: str) -> str:
    """Return terminal-safe display metadata without trusting installed package records."""

    if not isinstance(value, str):
        return fallback
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not _valid_plugin_text(normalized, maximum=maximum):
        return fallback
    return normalized


def builtin_registry() -> PluginRegistry:
    """Return a new registry containing only Rehearse built-ins."""

    from restore_rehearsal.importers import BUILTIN_IMPORTERS

    registry = PluginRegistry()
    for importer in BUILTIN_IMPORTERS:
        registry.register_importer(importer)
    return registry


def plugin_catalog(*, include_external: bool = True) -> tuple[dict[str, Any], ...]:
    descriptors = list(builtin_registry().descriptors())
    if include_external:
        descriptors.extend(PluginRegistry.external_descriptors())
    return tuple(
        {
            "id": item.id,
            "kind": item.kind,
            "version": item.version,
            "api_version": item.api_version,
            "description": item.description,
            "source": item.source,
            "enabled": item.enabled,
        }
        for item in sorted(descriptors, key=lambda value: (value.kind, value.id, value.source))
    )


def plugin_doctor(plugin_id: str, *, kind: str | None = None) -> dict[str, Any]:
    matches = [
        item
        for item in plugin_catalog(include_external=True)
        if item["id"] == plugin_id and (kind is None or item["kind"] == kind)
    ]
    if not matches:
        raise SafetyError("plugin was not found")
    if len(matches) > 1:
        raise SafetyError("plugin identifier is ambiguous; specify its kind")
    descriptor = matches[0]
    return {
        **descriptor,
        "compatible": descriptor["api_version"] in {PLUGIN_API_VERSION, "unknown"},
        "executed": False,
        "note": (
            "External plugin metadata was inspected without importing code; explicit activation "
            "is required to execute it."
            if descriptor["source"] != "builtin"
            else "Built-in plugin metadata and API compatibility are valid."
        ),
    }
