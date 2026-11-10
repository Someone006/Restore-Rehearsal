from __future__ import annotations

import stat
from pathlib import Path

from restore_rehearsal.brain.api import build_brain
from restore_rehearsal.brain.visual import _payload, write_visualization


def test_visual_v2_contains_private_accessible_views_and_snapshot_changes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    brain = tmp_path / "project.rrbrain"

    build_brain(project, brain)
    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    build_brain(project, brain)
    output = tmp_path / "brain.html"
    result = write_visualization(brain, output)
    html = output.read_text(encoding="utf-8")
    payload = _payload(brain)

    assert result["nodes"] >= 2
    assert len(payload["snapshots"]) >= 2
    assert payload["changes"]["counts"]["modified"] >= 1
    assert payload["metadata"]["schema_version"] == "2"
    for view in ("Graph", "Changes", "Findings", "Recovery", "Evidence", "Timeline"):
        assert f">{view}</button>" in html
    assert 'role="tablist"' in html
    assert 'role="tabpanel"' in html
    assert "Private offline export" in html
    assert "connect-src 'none'" in html
    assert "img-src 'none'" in html
    assert "'unsafe-inline'" not in html
    assert "https://" not in html
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
