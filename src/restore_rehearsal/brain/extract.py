"""Deterministic, non-executing graph extractors for common project artifacts."""

from __future__ import annotations

import ast
import json
import posixpath
import re
import tomllib
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any

import yaml

from restore_rehearsal.brain.inventory import Inventory, ScanFile
from restore_rehearsal.brain.model import Edge, Graph, Node

LANGUAGES = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".go": "Go",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}

COMPOSE_NAMES = re.compile(r"^(?:docker-)?compose(?:[.-].+)?\.ya?ml$", re.IGNORECASE)
RELATIVE_IMPORT = re.compile(r"(?:\bfrom\s*|\brequire\s*\(\s*)['\"](\.[^'\"]+)['\"]")
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\((?!https?://|mailto:|#)([^)#]+)(?:#[^)]+)?\)")
ENTITY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")
ACTION_REFERENCE = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?@[A-Za-z0-9_.:/+-]{1,200}$"
)


class NoAliasSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML aliases are disabled in project-brain extraction")
        return super().compose_node(parent, index)


class AccessibilityParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_has_lang = False
        self.images = 0
        self.images_missing_alt = 0
        self.controls = 0
        self.controls_missing_name = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs}
        lowered = tag.lower()
        if lowered == "html" and values.get("lang"):
            self.html_has_lang = True
        if lowered == "img":
            self.images += 1
            if "alt" not in values:
                self.images_missing_alt += 1
        if lowered in {"button", "input", "select", "textarea"}:
            self.controls += 1
            if not any(values.get(key) for key in ("aria-label", "aria-labelledby", "title", "id")):
                self.controls_missing_name += 1


def extract_graph(inventory: Inventory) -> Graph:
    graph = Graph(
        metadata={
            "git_commit": inventory.commit or "unborn",
            "inventory_files": str(len(inventory.files)),
            "inventory_read_bytes": str(inventory.read_bytes),
            "unsafe_names_skipped": str(inventory.skipped_unsafe_names),
        }
    )
    graph.add_node(Node(key="project:root", kind="project", name="project"))
    by_path = {item.path: item for item in inventory.files}
    file_keys: dict[str, str] = {}
    for item in inventory.files:
        kind = _file_kind(item)
        key = f"file:{item.path}"
        file_keys[item.path] = key
        language = LANGUAGES.get(PurePosixPath(item.path).suffix.lower())
        graph.add_node(
            Node(
                key=key,
                kind=kind,
                name=PurePosixPath(item.path).name,
                path=item.path,
                language=language,
                sha256=item.sha256,
                properties={
                    "size": item.size,
                    "tracked": item.tracked,
                    "sensitive": item.sensitive,
                    "skipped": item.skipped_reason,
                },
            )
        )
        component_key = _component_key(item.path)
        if component_key:
            component_name = PurePosixPath(item.path).parts[0]
            graph.add_node(Node(key=component_key, kind="component", name=component_name))
            graph.add_edge(
                Edge(
                    source="project:root",
                    target=component_key,
                    kind="contains",
                    provenance="extracted",
                )
            )
            parent = component_key
        else:
            parent = "project:root"
        graph.add_edge(Edge(source=parent, target=key, kind="contains", provenance="extracted"))

    python_modules = _python_modules(by_path)
    for item in inventory.files:
        if item.text is None:
            continue
        suffix = PurePosixPath(item.path).suffix.lower()
        if suffix == ".py":
            _extract_python(graph, item, python_modules)
        elif suffix in {".js", ".jsx", ".mjs", ".ts", ".tsx"}:
            _extract_relative_imports(graph, item, by_path)
        elif suffix == ".md":
            _extract_markdown_links(graph, item, by_path)
        if COMPOSE_NAMES.fullmatch(PurePosixPath(item.path).name):
            _extract_compose(graph, item)
        if _is_workflow(item.path):
            _extract_workflow(graph, item)
        if PurePosixPath(item.path).name == "pyproject.toml":
            _extract_pyproject(graph, item)
        if PurePosixPath(item.path).name == "package.json":
            _extract_package_json(graph, item)
        if PurePosixPath(item.path).name.lower().startswith("requirements"):
            _extract_requirements(graph, item)
        if suffix == ".html":
            _extract_html(graph, item)
        if suffix in {".css", ".scss"}:
            _extract_styles(graph, item)
        if PurePosixPath(item.path).name in {"rehearse.yaml", "rehearse.yml"}:
            _extract_rehearse_recipe(graph, item)
    return graph


def _extract_python(graph: Graph, item: ScanFile, modules: dict[str, str]) -> None:
    try:
        tree = ast.parse(item.text or "", filename=item.path)
    except (SyntaxError, ValueError, RecursionError):
        return
    source_key = f"file:{item.path}"
    for child in tree.body:
        if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "class" if isinstance(child, ast.ClassDef) else "function"
            symbol_key = f"symbol:{item.path}:{kind}:{child.name}"
            graph.add_node(
                Node(
                    key=symbol_key,
                    kind=kind,
                    name=child.name,
                    path=item.path,
                    line=child.lineno,
                    language="Python",
                )
            )
            graph.add_edge(
                Edge(
                    source=source_key,
                    target=symbol_key,
                    kind="declares",
                    provenance="extracted",
                    evidence_path=item.path,
                    evidence_line=child.lineno,
                )
            )
    for child in ast.walk(tree):
        names: list[str] = []
        if isinstance(child, ast.Import):
            names = [alias.name for alias in child.names]
        elif isinstance(child, ast.ImportFrom) and child.module:
            names = [child.module]
        for name in names:
            target = _resolve_python_module(name, modules)
            if target:
                relation = "tests" if _is_test_path(item.path) else "imports"
                graph.add_edge(
                    Edge(
                        source=source_key,
                        target=f"file:{target}",
                        kind=relation,
                        provenance="extracted",
                        evidence_path=item.path,
                        evidence_line=getattr(child, "lineno", 1),
                    )
                )


def _extract_relative_imports(graph: Graph, item: ScanFile, files: dict[str, ScanFile]) -> None:
    source = PurePosixPath(item.path)
    for match in RELATIVE_IMPORT.finditer(item.text or ""):
        candidate = PurePosixPath(posixpath.normpath(str(source.parent / match.group(1))))
        choices = [
            str(candidate),
            *(f"{candidate}{extension}" for extension in (".ts", ".tsx", ".js", ".jsx", ".mjs")),
            *(str(candidate / f"index{extension}") for extension in (".ts", ".tsx", ".js", ".jsx")),
        ]
        target = next((choice for choice in choices if choice in files), None)
        if target:
            graph.add_edge(
                Edge(
                    source=f"file:{item.path}",
                    target=f"file:{target}",
                    kind="tests" if _is_test_path(item.path) else "imports",
                    provenance="extracted",
                    evidence_path=item.path,
                    evidence_line=(item.text or "")[: match.start()].count("\n") + 1,
                )
            )


def _extract_markdown_links(graph: Graph, item: ScanFile, files: dict[str, ScanFile]) -> None:
    source = PurePosixPath(item.path)
    for match in MARKDOWN_LINK.finditer(item.text or ""):
        target = posixpath.normpath(str(source.parent / match.group(1)))
        if target in files:
            graph.add_edge(
                Edge(
                    source=f"file:{item.path}",
                    target=f"file:{target}",
                    kind="documents",
                    provenance="extracted",
                    evidence_path=item.path,
                    evidence_line=(item.text or "")[: match.start()].count("\n") + 1,
                )
            )


def _extract_compose(graph: Graph, item: ScanFile) -> None:
    data = _yaml(item.text or "")
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        return
    compose_key = f"file:{item.path}"
    valid_services = sorted(
        (
            (name, raw)
            for name, raw in data["services"].items()
            if isinstance(name, str) and isinstance(raw, dict)
        ),
        key=lambda item: item[0],
    )
    for name, raw in valid_services:
        if not ENTITY_NAME.fullmatch(name):
            continue
        service_key = _service_key(item.path, name)
        volumes = raw.get("volumes") if isinstance(raw.get("volumes"), list) else []
        properties = {
            "has_healthcheck": bool(raw.get("healthcheck")),
            "has_published_ports": bool(raw.get("ports")),
            "privileged": bool(raw.get("privileged")),
            "volume_count": len(volumes),
        }
        image = raw.get("image")
        if isinstance(image, str) and not _credentialed(image):
            properties["image"] = image[:200]
        graph.add_node(Node(key=service_key, kind="service", name=name, properties=properties))
        graph.add_edge(
            Edge(
                source=compose_key,
                target=service_key,
                kind="defines",
                provenance="declared",
                evidence_path=item.path,
            )
        )

    for name, raw in valid_services:
        if not ENTITY_NAME.fullmatch(name):
            continue
        service_key = _service_key(item.path, name)
        volumes = raw.get("volumes") if isinstance(raw.get("volumes"), list) else []
        depends = raw.get("depends_on") or []
        dependency_names = (
            depends
            if isinstance(depends, list)
            else list(depends)
            if isinstance(depends, dict)
            else []
        )
        for dependency in dependency_names:
            if isinstance(dependency, str):
                dependency_key = _service_key(item.path, dependency)
                if dependency_key in graph.nodes:
                    graph.add_edge(
                        Edge(
                            source=service_key,
                            target=dependency_key,
                            kind="depends-on",
                            provenance="declared",
                            evidence_path=item.path,
                        )
                    )
        for volume in volumes:
            source = (
                volume.split(":", 1)[0]
                if isinstance(volume, str)
                else volume.get("source")
                if isinstance(volume, dict)
                else None
            )
            if (
                isinstance(source, str)
                and ENTITY_NAME.fullmatch(source)
                and not source.startswith((".", "/"))
            ):
                volume_key = f"volume:{source}"
                graph.add_node(Node(key=volume_key, kind="data-store", name=source))
                graph.add_edge(
                    Edge(
                        source=service_key,
                        target=volume_key,
                        kind="stores-data-in",
                        provenance="declared",
                        evidence_path=item.path,
                    )
                )


def _extract_workflow(graph: Graph, item: ScanFile) -> None:
    data = _yaml(item.text or "")
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        return
    workflow = f"file:{item.path}"
    jobs = sorted(
        (
            (name, raw)
            for name, raw in data["jobs"].items()
            if isinstance(name, str) and isinstance(raw, dict)
        ),
        key=lambda item: item[0],
    )
    for job_name, raw in jobs:
        if not ENTITY_NAME.fullmatch(job_name):
            continue
        job_key = f"ci-job:{item.path}:{job_name}"
        steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
        has_tests = any(
            isinstance(step, dict)
            and isinstance(step.get("run"), str)
            and re.search(r"\b(pytest|npm\s+(?:run\s+)?test|cargo\s+test|go\s+test)\b", step["run"])
            for step in steps
        )
        graph.add_node(
            Node(key=job_key, kind="ci-job", name=job_name, properties={"has_tests": has_tests})
        )
        graph.add_edge(
            Edge(
                source=workflow,
                target=job_key,
                kind="defines",
                provenance="declared",
                evidence_path=item.path,
            )
        )
        for step in steps:
            uses = step.get("uses") if isinstance(step, dict) else None
            if (
                not isinstance(uses, str)
                or not ACTION_REFERENCE.fullmatch(uses)
                or _credentialed(uses)
            ):
                continue
            action_key = f"dependency:github-action:{uses}"
            pinned = bool(re.search(r"@[0-9a-fA-F]{40}$", uses))
            graph.add_node(
                Node(
                    key=action_key,
                    kind="dependency",
                    name=uses[:200],
                    properties={"pinned": pinned, "ecosystem": "github-actions"},
                )
            )
            graph.add_edge(
                Edge(
                    source=job_key,
                    target=action_key,
                    kind="uses",
                    provenance="declared",
                    evidence_path=item.path,
                )
            )


def _extract_pyproject(graph: Graph, item: ScanFile) -> None:
    try:
        data = tomllib.loads(item.text or "")
    except (tomllib.TOMLDecodeError, ValueError):
        return
    project = data.get("project") if isinstance(data, dict) else None
    dependencies = project.get("dependencies", []) if isinstance(project, dict) else []
    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    values = list(dependencies) if isinstance(dependencies, list) else []
    if isinstance(optional, dict):
        for group in optional.values():
            if isinstance(group, list):
                values.extend(group)
    for raw in values:
        name = _package_name(raw)
        if name:
            _dependency(graph, f"file:{item.path}", "python", name, item.path)


def _extract_package_json(graph: Graph, item: ScanFile) -> None:
    try:
        data = json.loads(item.text or "")
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        values = data.get(field)
        if not isinstance(values, dict):
            continue
        for name in values:
            if isinstance(name, str) and re.fullmatch(
                r"@?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?", name
            ):
                _dependency(graph, f"file:{item.path}", "npm", name, item.path)


def _extract_requirements(graph: Graph, item: ScanFile) -> None:
    for line in (item.text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-", "git+")) or "://" in stripped:
            continue
        name = _package_name(stripped)
        if name:
            _dependency(graph, f"file:{item.path}", "python", name, item.path)


def _extract_html(graph: Graph, item: ScanFile) -> None:
    parser = AccessibilityParser()
    try:
        parser.feed(item.text or "")
    except Exception:
        return
    key = f"file:{item.path}"
    node = graph.nodes[key]
    graph.nodes[key] = Node(
        **{
            **node.__dict__,
            "properties": {
                **node.properties,
                "html_has_lang": parser.html_has_lang,
                "images": parser.images,
                "images_missing_alt": parser.images_missing_alt,
                "controls": parser.controls,
                "controls_missing_name": parser.controls_missing_name,
            },
        }
    )


def _extract_styles(graph: Graph, item: ScanFile) -> None:
    key = f"file:{item.path}"
    node = graph.nodes[key]
    text = item.text or ""
    graph.nodes[key] = Node(
        **{
            **node.__dict__,
            "properties": {
                **node.properties,
                "custom_properties": len(re.findall(r"--[A-Za-z0-9_-]+\s*:", text)),
                "literal_colors": len(re.findall(r"#[0-9A-Fa-f]{3,8}\b", text)),
            },
        }
    )


def _extract_rehearse_recipe(graph: Graph, item: ScanFile) -> None:
    data = _yaml(item.text or "")
    if not isinstance(data, dict) or data.get("version") != 1:
        return
    recipe_name = data.get("name")
    if not isinstance(recipe_name, str) or not ENTITY_NAME.fullmatch(recipe_name):
        return
    recipe_key = f"rehearsal:{item.path}:{recipe_name}"
    graph.add_node(Node(key=recipe_key, kind="rehearsal", name=recipe_name))
    graph.add_edge(
        Edge(
            source=f"file:{item.path}",
            target=recipe_key,
            kind="defines",
            provenance="declared",
            evidence_path=item.path,
        )
    )
    checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    for check in checks:
        check_name = check.get("name") if isinstance(check, dict) else None
        if isinstance(check_name, str) and ENTITY_NAME.fullmatch(check_name):
            check_key = f"check:{item.path}:{recipe_name}:{check_name}"
            graph.add_node(Node(key=check_key, kind="verification", name=check_name))
            graph.add_edge(
                Edge(
                    source=recipe_key,
                    target=check_key,
                    kind="verifies-with",
                    provenance="declared",
                    evidence_path=item.path,
                )
            )


def _dependency(graph: Graph, source: str, ecosystem: str, name: str, path: str) -> None:
    key = f"dependency:{ecosystem}:{name.lower()}"
    graph.add_node(Node(key=key, kind="dependency", name=name, properties={"ecosystem": ecosystem}))
    graph.add_edge(
        Edge(
            source=source,
            target=key,
            kind="declares-dependency",
            provenance="declared",
            evidence_path=path,
        )
    )


def _file_kind(item: ScanFile) -> str:
    path = PurePosixPath(item.path)
    name = path.name.lower()
    suffix = path.suffix.lower()
    if item.is_symlink:
        return "symlink"
    if item.sensitive:
        return "sensitive-file"
    if _is_test_path(item.path):
        return "test"
    if _is_workflow(item.path):
        return "workflow"
    if COMPOSE_NAMES.fullmatch(path.name):
        return "deployment"
    if name in {"license", "license.md", "license.txt", "notice", "notice.txt"}:
        return "license"
    if name in {"security.md", "privacy.md", "contributing.md", "code_of_conduct.md", "support.md"}:
        return "policy"
    if name in {
        "pyproject.toml",
        "package.json",
        "cargo.toml",
        "go.mod",
        "requirements.txt",
        "uv.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    }:
        return "manifest"
    if suffix in {".html", ".jsx", ".tsx", ".vue", ".svelte"}:
        return "ui"
    if suffix in {".css", ".scss"}:
        return "style"
    if suffix == ".md":
        return "documentation"
    if suffix in LANGUAGES:
        return "source"
    return "file"


def _component_key(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    return f"component:{parts[0]}" if len(parts) > 1 else None


def _service_key(compose_path: str, name: str) -> str:
    return f"service:{compose_path}:{name}"


def _python_modules(files: dict[str, ScanFile]) -> dict[str, str]:
    modules: dict[str, str] = {}
    for path in files:
        pure = PurePosixPath(path)
        if pure.suffix != ".py":
            continue
        without_suffix = pure.with_suffix("")
        parts = list(without_suffix.parts)
        if parts[-1] == "__init__":
            parts.pop()
        variants = [parts]
        if parts and parts[0] in {"src", "lib"}:
            variants.append(parts[1:])
        for variant in variants:
            if variant:
                modules[".".join(variant)] = path
    return modules


def _resolve_python_module(name: str, modules: dict[str, str]) -> str | None:
    candidate = name
    while candidate:
        if candidate in modules:
            return modules[candidate]
        candidate = candidate.rpartition(".")[0]
    return None


def _package_name(raw: Any) -> str | None:
    if not isinstance(raw, str) or _credentialed(raw):
        return None
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)", raw.strip())
    return match.group(1) if match else None


def _yaml(text: str) -> Any:
    try:
        return yaml.load(text, Loader=NoAliasSafeLoader)
    except (yaml.YAMLError, RecursionError):
        return None


def _credentialed(value: str) -> bool:
    return bool(re.search(r"(?i)https?://[^/@\s:]+:[^/@\s]+@", value))


def _is_workflow(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        len(pure.parts) >= 3
        and pure.parts[:2] == (".github", "workflows")
        and pure.suffix in {".yml", ".yaml"}
    )


def _is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name.lower()
    return (
        "tests" in pure.parts
        or "test" in pure.parts
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )
