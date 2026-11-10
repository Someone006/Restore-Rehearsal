"""Self-contained, offline project-brain graph visualization."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

from restore_rehearsal.brain.store import open_brain
from restore_rehearsal.errors import EnvironmentError, SafetyError

MAX_VISUAL_NODES = 1_500
MAX_VISUAL_EDGES = 3_000
MAX_VISUAL_CHANGES = 500
MAX_VISUAL_BYTES = 8 * 1024 * 1024


def write_visualization(brain: Path, output: Path) -> dict[str, Any]:
    payload = _payload(brain)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    encoded = (
        encoded.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    if len(encoded.encode("utf-8")) > MAX_VISUAL_BYTES:
        raise SafetyError("visualization payload exceeds the private export limit")
    nonce = secrets.token_urlsafe(24)
    html = HTML_V2.replace("__BRAIN_DATA__", encoded).replace("__CSP_NONCE__", nonce)
    target = output.expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    try:
        parent = target.parent.resolve(strict=True)
    except OSError as exc:
        raise EnvironmentError("visualization parent directory does not exist") from exc
    target = parent / target.name
    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    temporary.chmod(0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(html)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise EnvironmentError("visualization path exists; refusing to overwrite it") from exc
        except OSError as exc:
            raise EnvironmentError("could not create visualization") from exc
        _fsync_directory(parent)
    except BaseException:
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "output": target.name,
        "nodes": len(payload["nodes"]),
        "edges": len(payload["edges"]),
        "truncated": payload["truncated"],
    }


def _payload(brain: Path) -> dict[str, Any]:
    with open_brain(brain) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
            )
        }
        metadata = dict(
            connection.execute(
                "SELECT key, value FROM metadata WHERE key IN "
                "('format', 'schema_version', 'generator_version', 'generated_at', "
                "'semantic_digest', 'inventory_mode', 'git_commit') ORDER BY key"
            )
        )
        prioritized = connection.execute(
            """SELECT key, kind, name, path, language, properties_json
            FROM nodes ORDER BY CASE kind
                WHEN 'project' THEN 0 WHEN 'component' THEN 1 WHEN 'service' THEN 2
                WHEN 'data-store' THEN 3 WHEN 'rehearsal' THEN 4 WHEN 'test' THEN 5
                WHEN 'ui' THEN 6 ELSE 7 END, kind, name, key LIMIT ?""",
            (MAX_VISUAL_NODES,),
        ).fetchall()
        keys = {row["key"] for row in prioritized}
        nodes = [
            {
                "key": row["key"],
                "kind": row["kind"],
                "name": row["name"],
                "path": row["path"],
                "language": row["language"],
            }
            for row in prioritized
        ]
        edges: list[dict[str, Any]] = []
        for row in connection.execute(
            """SELECT source_key, target_key, kind, provenance
            FROM edges ORDER BY source_key, target_key, kind LIMIT ?""",
            (MAX_VISUAL_EDGES * 3,),
        ):
            if row["source_key"] in keys and row["target_key"] in keys:
                edges.append(
                    {
                        "source": row["source_key"],
                        "target": row["target_key"],
                        "kind": row["kind"],
                        "provenance": row["provenance"],
                    }
                )
                if len(edges) >= MAX_VISUAL_EDGES:
                    break
        findings = [
            dict(row)
            for row in connection.execute(
                """SELECT fingerprint, lens, severity, title, description, remediation,
                    confidence, node_key, evidence_path, evidence_line
                FROM findings WHERE status='open'
                ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                WHEN 'medium' THEN 2 ELSE 3 END, lens, title LIMIT 500"""
            )
        ]
        total_nodes = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        total_edges = connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        memories = [
            dict(row)
            for row in connection.execute(
                """SELECT id, kind, title, path, status, created_at
                FROM memories ORDER BY created_at DESC LIMIT 100"""
            )
        ]
        runs = [
            dict(row)
            for row in connection.execute(
                """SELECT run_id, recipe, status, cleanup, started_at, finished_at
                FROM runs ORDER BY started_at DESC LIMIT 100"""
            )
        ]
        snapshots: list[dict[str, Any]] = []
        changes: dict[str, Any] = {
            "from": None,
            "to": None,
            "nodes": [],
            "counts": {"added": 0, "removed": 0, "modified": 0},
            "truncated": False,
        }
        if {"snapshots", "snapshot_nodes"} <= table_names:
            snapshots = [
                dict(row)
                for row in connection.execute(
                    """SELECT id, sequence, created_at, label, semantic_digest, git_commit,
                        inventory_mode, node_count, edge_count, finding_count
                    FROM snapshots ORDER BY sequence DESC LIMIT 20"""
                )
            ]
            if len(snapshots) >= 2:
                changes = _snapshot_changes(connection, snapshots[1]["id"], snapshots[0]["id"])
        imports: list[dict[str, Any]] = []
        if "imports" in table_names:
            imports = [
                dict(row)
                for row in connection.execute(
                    """SELECT id, kind, source_name, importer_id, importer_version, imported_at
                    FROM imports ORDER BY imported_at DESC, id DESC LIMIT 100"""
                )
            ]
    return {
        "metadata": metadata,
        "nodes": nodes,
        "edges": edges,
        "findings": findings,
        "memories": memories,
        "runs": runs,
        "snapshots": snapshots,
        "changes": changes,
        "imports": imports,
        "totals": {"nodes": total_nodes, "edges": total_edges},
        "truncated": total_nodes > len(nodes) or total_edges > len(edges),
    }


def _snapshot_changes(connection: Any, previous_id: str, current_id: str) -> dict[str, Any]:
    previous = {
        row["node_key"]: dict(row)
        for row in connection.execute(
            """SELECT node_key, kind, name, path, content_digest
            FROM snapshot_nodes WHERE snapshot_id=? ORDER BY node_key""",
            (previous_id,),
        )
    }
    current = {
        row["node_key"]: dict(row)
        for row in connection.execute(
            """SELECT node_key, kind, name, path, content_digest
            FROM snapshot_nodes WHERE snapshot_id=? ORDER BY node_key""",
            (current_id,),
        )
    }
    added = sorted(current.keys() - previous.keys())
    removed = sorted(previous.keys() - current.keys())
    modified = sorted(
        key
        for key in current.keys() & previous.keys()
        if current[key]["content_digest"] != previous[key]["content_digest"]
    )
    rows: list[dict[str, Any]] = []
    for change, keys, source in (
        ("added", added, current),
        ("removed", removed, previous),
        ("modified", modified, current),
    ):
        for key in keys:
            item = source[key]
            rows.append(
                {
                    "change": change,
                    "key": key,
                    "kind": item["kind"],
                    "name": item["name"],
                    "path": item["path"],
                }
            )
    return {
        "from": previous_id,
        "to": current_id,
        "nodes": rows[:MAX_VISUAL_CHANGES],
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
        },
        "truncated": len(rows) > MAX_VISUAL_CHANGES,
    }


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-__CSP_NONCE__'; style-src 'nonce-__CSP_NONCE__'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>Rehearse Project Brain</title>
<style nonce="__CSP_NONCE__">
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a30;--line:#34415f;--text:#eef3ff;--muted:#aab6d3;--focus:#72e0d1;--critical:#ff4d6d;--high:#ff9f43;--medium:#feca57;--low:#54a0ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,sans-serif}header{display:flex;gap:12px;align-items:center;padding:12px 16px;background:var(--panel);border-bottom:1px solid var(--line)}h1{font-size:17px;margin:0}input,select,button{background:#0d1529;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:7px}button:focus,input:focus,select:focus{outline:2px solid var(--focus);outline-offset:2px}.layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;height:calc(100vh - 58px)}main{position:relative;overflow:hidden}canvas{display:block;width:100%;height:100%}aside{overflow:auto;background:var(--panel);border-left:1px solid var(--line);padding:14px}h2{font-size:15px;margin:14px 0 7px}.muted{color:var(--muted)}.badge{display:inline-block;padding:2px 7px;border-radius:999px;background:#24304d;margin:2px}.finding{border-left:4px solid var(--low);background:#0d1529;padding:9px;margin:8px 0}.finding[data-severity="critical"]{border-color:var(--critical)}.finding[data-severity="high"]{border-color:var(--high)}.finding[data-severity="medium"]{border-color:var(--medium)}table{border-collapse:collapse;width:100%;font-size:12px}td,th{text-align:left;border-bottom:1px solid var(--line);padding:6px}#tableWrap{max-height:300px;overflow:auto}.sr{position:absolute;left:-9999px}@media(max-width:850px){.layout{grid-template-columns:1fr;display:block;height:auto}main{height:60vh}aside{border-left:0;border-top:1px solid var(--line)}}
</style>
</head>
<body>
<header><h1>Rehearse Project Brain</h1><label>Search <input id="search" type="search" autocomplete="off"></label><label>Kind <select id="kind"><option value="">All</option></select></label><button id="reset" type="button">Reset view</button><span id="summary" class="muted"></span></header>
<div class="layout"><main><canvas id="graph" role="img" aria-label="Interactive project relationship graph"></canvas></main><aside><div id="detail"><h2>Project evidence graph</h2><p class="muted">Select a node or use the accessible table. Colors identify artifact kinds; severity rings identify findings.</p></div><h2>Open findings</h2><div id="findings"></div><h2>Recent evidence</h2><div id="evidence"></div><h2>Accessible node list</h2><div id="tableWrap"><table><thead><tr><th>Artifact</th><th>Kind</th></tr></thead><tbody id="nodeTable"></tbody></table></div></aside></div>
<script id="brain-data" type="application/json" nonce="__CSP_NONCE__">__BRAIN_DATA__</script>
<script nonce="__CSP_NONCE__">
'use strict';
const data=JSON.parse(document.getElementById('brain-data').textContent);const canvas=document.getElementById('graph'),ctx=canvas.getContext('2d');const search=document.getElementById('search'),kind=document.getElementById('kind'),detail=document.getElementById('detail'),findingBox=document.getElementById('findings'),table=document.getElementById('nodeTable');
const palette=['#72e0d1','#54a0ff','#c56cf0','#ff9f43','#feca57','#1dd1a1','#ff6b6b','#5f27cd','#48dbfb','#a3cb38'];const kinds=[...new Set(data.nodes.map(n=>n.kind))].sort();kinds.forEach(k=>{const o=document.createElement('option');o.value=k;o.textContent=k;kind.appendChild(o)});const color=new Map(kinds.map((k,i)=>[k,palette[i%palette.length]]));let scale=1,ox=0,oy=0,selected=null,drag=null;const positions=new Map();
function hash(s){let h=2166136261;for(const c of s){h^=c.charCodeAt(0);h=Math.imul(h,16777619)}return h>>>0}function layout(){const groups=new Map();for(const n of data.nodes){if(!groups.has(n.kind))groups.set(n.kind,[]);groups.get(n.kind).push(n)}const w=canvas.clientWidth,h=canvas.clientHeight,entries=[...groups.entries()].sort();entries.forEach(([k,nodes],gi)=>{const a=2*Math.PI*gi/Math.max(entries.length,1)-Math.PI/2,cx=w/2+Math.cos(a)*w*.28,cy=h/2+Math.sin(a)*h*.28;nodes.sort((a,b)=>a.key.localeCompare(b.key)).forEach((n,i)=>{const r=18+Math.sqrt(i)*13,ang=(hash(n.key)%6283)/1000;positions.set(n.key,{x:cx+Math.cos(ang)*r,y:cy+Math.sin(ang)*r})})})}function visible(n){const q=search.value.trim().toLowerCase();return(!kind.value||n.kind===kind.value)&&(!q||`${n.name} ${n.path||''} ${n.kind}`.toLowerCase().includes(q))}function resize(){const d=devicePixelRatio||1;canvas.width=canvas.clientWidth*d;canvas.height=canvas.clientHeight*d;ctx.setTransform(d,0,0,d,0,0);layout();draw()}function point(p){return{x:p.x*scale+ox,y:p.y*scale+oy}}function draw(){ctx.clearRect(0,0,canvas.clientWidth,canvas.clientHeight);const shown=new Set(data.nodes.filter(visible).map(n=>n.key));ctx.save();ctx.strokeStyle='#34415f';ctx.globalAlpha=.42;for(const e of data.edges){if(!shown.has(e.source)||!shown.has(e.target))continue;const a=point(positions.get(e.source)),b=point(positions.get(e.target));ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke()}ctx.globalAlpha=1;for(const n of data.nodes){if(!shown.has(n.key))continue;const p=point(positions.get(n.key)),findings=data.findings.filter(f=>f.node_key===n.key);ctx.beginPath();ctx.arc(p.x,p.y,n.key===selected?8:5,0,Math.PI*2);ctx.fillStyle=color.get(n.kind);ctx.fill();if(findings.length){ctx.beginPath();ctx.arc(p.x,p.y,10,0,Math.PI*2);ctx.strokeStyle=findings.some(f=>f.severity==='critical')?'#ff4d6d':findings.some(f=>f.severity==='high')?'#ff9f43':'#feca57';ctx.lineWidth=2;ctx.stroke();ctx.lineWidth=1}}ctx.restore();document.getElementById('summary').textContent=`${shown.size}/${data.totals.nodes} nodes · ${data.totals.edges} edges${data.truncated?' · view truncated':''}`}
function selectNode(key){selected=key;const n=data.nodes.find(x=>x.key===key);if(!n)return;detail.replaceChildren();const h=document.createElement('h2');h.textContent=n.name;detail.append(h);for(const value of [n.kind,n.path,n.language].filter(Boolean)){const b=document.createElement('span');b.className='badge';b.textContent=value;detail.append(b)}const related=data.edges.filter(e=>e.source===key||e.target===key);const p=document.createElement('p');p.className='muted';p.textContent=`${related.length} visible relationships`;detail.append(p);const fs=data.findings.filter(f=>f.node_key===key);fs.forEach(f=>renderFinding(f,detail));draw()}function renderFinding(f,parent=findingBox){const d=document.createElement('div');d.className='finding';d.dataset.severity=f.severity;const t=document.createElement('strong');t.textContent=`${f.severity.toUpperCase()} · ${f.title}`;const p=document.createElement('p');p.textContent=f.description;const r=document.createElement('p');r.className='muted';r.textContent=`Suggested: ${f.remediation}`;d.append(t,p,r);parent.append(d)}findingBox.replaceChildren();data.findings.slice(0,80).forEach(f=>renderFinding(f));const evidence=document.getElementById('evidence');evidence.textContent=`${data.memories.length} memories · ${data.runs.length} rehearsal runs`;function buildTable(){table.replaceChildren();data.nodes.slice(0,500).forEach(n=>{const tr=document.createElement('tr'),a=document.createElement('button'),td1=document.createElement('td'),td2=document.createElement('td');a.type='button';a.textContent=n.name;a.addEventListener('click',()=>selectNode(n.key));td1.append(a);td2.textContent=n.kind;tr.append(td1,td2);table.append(tr)})}buildTable();canvas.addEventListener('click',e=>{let best=null,dist=14;for(const n of data.nodes.filter(visible)){const p=point(positions.get(n.key)),d=Math.hypot(e.offsetX-p.x,e.offsetY-p.y);if(d<dist){best=n.key;dist=d}}if(best)selectNode(best)});canvas.addEventListener('wheel',e=>{e.preventDefault();const f=e.deltaY<0?1.1:.9;scale=Math.max(.25,Math.min(4,scale*f));draw()},{passive:false});canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,ox,oy};canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!drag)return;ox=drag.ox+e.clientX-drag.x;oy=drag.oy+e.clientY-drag.y;draw()});canvas.addEventListener('pointerup',()=>drag=null);search.addEventListener('input',draw);kind.addEventListener('change',draw);document.getElementById('reset').addEventListener('click',()=>{scale=1;ox=0;oy=0;search.value='';kind.value='';draw()});addEventListener('resize',resize);resize();
</script>
</body></html>
"""


HTML_V2 = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-__CSP_NONCE__'; style-src 'nonce-__CSP_NONCE__'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>Rehearse Project Brain</title>
<style nonce="__CSP_NONCE__">
:root{color-scheme:dark;--bg:#090e1a;--panel:#11192b;--panel2:#172238;--line:#32405f;--text:#f2f5ff;--muted:#a9b5ce;--focus:#6ee7d8;--accent:#7aa2ff;--critical:#ff5d76;--high:#ffad5c;--medium:#f6d365;--low:#61a8ff;--added:#4bd49b;--removed:#ff7184;--modified:#ffd166}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.48 system-ui,sans-serif}button,input,select{font:inherit}button,input,select{background:#0c1426;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:7px 9px}button{cursor:pointer}button:focus-visible,input:focus-visible,select:focus-visible,[tabindex]:focus-visible{outline:3px solid var(--focus);outline-offset:2px}.top{display:flex;gap:14px;align-items:center;justify-content:space-between;padding:13px 18px;background:var(--panel);border-bottom:1px solid var(--line)}h1{font-size:18px;margin:0}.privacy{color:var(--focus);font-size:12px}.tabs{display:flex;gap:4px;overflow:auto;padding:8px 14px;background:#0d1527;border-bottom:1px solid var(--line)}.tabs button{border-color:transparent;background:transparent;white-space:nowrap}.tabs button[aria-selected="true"]{background:var(--panel2);border-color:var(--line);color:var(--focus)}.panel{padding:16px;min-height:calc(100vh - 106px)}.toolbar{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin-bottom:12px}.toolbar label{display:grid;gap:4px;color:var(--muted);font-size:12px}.summary{margin-left:auto;color:var(--muted)}.graph-layout{display:grid;grid-template-columns:minmax(0,1fr) 350px;height:calc(100vh - 178px);min-height:500px;border:1px solid var(--line);border-radius:10px;overflow:hidden}.graph-main{position:relative;min-width:0;background:radial-gradient(circle at 50% 50%,#14213b 0,#090e1a 68%)}canvas{display:block;width:100%;height:100%}.side{overflow:auto;background:var(--panel);border-left:1px solid var(--line);padding:14px}h2{font-size:18px;margin:0 0 12px}h3{font-size:15px;margin:18px 0 8px}.muted{color:var(--muted)}.badge{display:inline-block;padding:2px 7px;border-radius:999px;background:#243150;margin:2px}.card-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.card,.finding,.timeline-item{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:12px}.finding{border-left:4px solid var(--low);margin:9px 0}.finding[data-severity="critical"]{border-left-color:var(--critical)}.finding[data-severity="high"]{border-left-color:var(--high)}.finding[data-severity="medium"]{border-left-color:var(--medium)}.change[data-change="added"]{color:var(--added)}.change[data-change="removed"]{color:var(--removed)}.change[data-change="modified"]{color:var(--modified)}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;border-bottom:1px solid var(--line);padding:8px;vertical-align:top}th{position:sticky;top:0;background:var(--panel);z-index:1}.table-wrap{max-height:56vh;overflow:auto;border:1px solid var(--line);border-radius:8px}.node-list{max-height:260px}.empty{color:var(--muted);padding:12px;border:1px dashed var(--line);border-radius:8px}.timeline{display:grid;gap:10px}.timeline time{color:var(--muted);font-size:12px}.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@media(max-width:880px){.top{align-items:flex-start;flex-direction:column}.panel{padding:10px}.graph-layout{display:block;height:auto}.graph-main{height:58vh;min-height:390px}.side{border-left:0;border-top:1px solid var(--line)}.summary{width:100%;margin-left:0}.table-wrap{max-height:48vh}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
</style>
</head>
<body>
<header class="top"><div><h1>Rehearse Project Brain</h1><div id="meta" class="muted"></div></div><div class="privacy">Private offline export · no network access</div></header>
<nav class="tabs" role="tablist" aria-label="Project Brain views">
<button id="tab-graph" role="tab" aria-selected="true" aria-controls="panel-graph" data-view="graph">Graph</button>
<button id="tab-changes" role="tab" aria-selected="false" aria-controls="panel-changes" data-view="changes" tabindex="-1">Changes</button>
<button id="tab-findings" role="tab" aria-selected="false" aria-controls="panel-findings" data-view="findings" tabindex="-1">Findings</button>
<button id="tab-recovery" role="tab" aria-selected="false" aria-controls="panel-recovery" data-view="recovery" tabindex="-1">Recovery</button>
<button id="tab-evidence" role="tab" aria-selected="false" aria-controls="panel-evidence" data-view="evidence" tabindex="-1">Evidence</button>
<button id="tab-timeline" role="tab" aria-selected="false" aria-controls="panel-timeline" data-view="timeline" tabindex="-1">Timeline</button>
</nav>
<section id="panel-graph" class="panel" role="tabpanel" aria-labelledby="tab-graph">
<div class="toolbar"><label>Search<input id="search" type="search" autocomplete="off"></label><label>Artifact kind<select id="kind"><option value="">All kinds</option></select></label><label>Relationship<select id="edgeKind"><option value="">All relationships</option></select></label><button id="reset" type="button">Reset view</button><span id="summary" class="summary"></span></div>
<div class="graph-layout"><div class="graph-main"><canvas id="graph" role="img" aria-label="Interactive project relationship graph; use the adjacent table for keyboard access"></canvas></div><aside class="side"><div id="detail"><h2>Project evidence graph</h2><p class="muted">Select a node or use the accessible artifact table.</p></div><h3>Accessible artifact list</h3><div class="table-wrap node-list"><table><thead><tr><th>Artifact</th><th>Kind</th></tr></thead><tbody id="nodeTable"></tbody></table></div></aside></div>
</section>
<section id="panel-changes" class="panel" role="tabpanel" aria-labelledby="tab-changes" hidden><div class="toolbar"><label>Snapshot<select id="snapshot"></select></label><span id="changeSummary" class="summary"></span></div><div id="snapshotDetail" class="card"></div><h2>Latest structural changes</h2><div id="changes"></div></section>
<section id="panel-findings" class="panel" role="tabpanel" aria-labelledby="tab-findings" hidden><div class="toolbar"><label>Severity<select id="severity"><option value="">All severities</option><option>critical</option><option>high</option><option>medium</option><option>low</option></select></label><label>Lens<select id="lens"><option value="">All lenses</option></select></label><span id="findingSummary" class="summary"></span></div><div id="findings"></div></section>
<section id="panel-recovery" class="panel" role="tabpanel" aria-labelledby="tab-recovery" hidden><h2>Recovery readiness</h2><p class="muted">Evidence only; this view is not a recovery certification.</p><div id="recovery" class="card-grid"></div></section>
<section id="panel-evidence" class="panel" role="tabpanel" aria-labelledby="tab-evidence" hidden><h2>Recorded evidence</h2><div id="evidence" class="card-grid"></div></section>
<section id="panel-timeline" class="panel" role="tabpanel" aria-labelledby="tab-timeline" hidden><h2>Project timeline</h2><div id="timeline" class="timeline"></div></section>
<script id="brain-data" type="application/json" nonce="__CSP_NONCE__">__BRAIN_DATA__</script>
<script nonce="__CSP_NONCE__">
'use strict';
const data=JSON.parse(document.getElementById('brain-data').textContent);const byId=id=>document.getElementById(id);const tabs=[...document.querySelectorAll('[role="tab"]')];
function activate(view){for(const tab of tabs){const on=tab.dataset.view===view;tab.setAttribute('aria-selected',String(on));tab.tabIndex=on?0:-1;byId(`panel-${tab.dataset.view}`).hidden=!on}if(view==='graph')resize()}
tabs.forEach((tab,index)=>{tab.addEventListener('click',()=>activate(tab.dataset.view));tab.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();let next=index+(event.key==='ArrowRight'?1:-1);if(event.key==='Home')next=0;if(event.key==='End')next=tabs.length-1;next=(next+tabs.length)%tabs.length;tabs[next].focus();activate(tabs[next].dataset.view)})});
byId('meta').textContent=`${data.metadata.generator_version?'v'+data.metadata.generator_version+' · ':''}${data.totals.nodes} artifacts · ${data.totals.edges} relationships`;
const canvas=byId('graph'),ctx=canvas.getContext('2d'),search=byId('search'),kind=byId('kind'),edgeKind=byId('edgeKind'),detail=byId('detail'),table=byId('nodeTable');const palette=['#6ee7d8','#61a8ff','#c77dff','#ffad5c','#f6d365','#4bd49b','#ff7184','#8d7aff','#55d6e8','#b6d957'];const kinds=[...new Set(data.nodes.map(n=>n.kind))].sort();const edgeKinds=[...new Set(data.edges.map(e=>e.kind))].sort();for(const value of kinds){const option=document.createElement('option');option.value=value;option.textContent=value;kind.append(option)}for(const value of edgeKinds){const option=document.createElement('option');option.value=value;option.textContent=value;edgeKind.append(option)}const colors=new Map(kinds.map((value,index)=>[value,palette[index%palette.length]]));const positions=new Map();let scale=1,offsetX=0,offsetY=0,selected=null,drag=null;
function hash(value){let result=2166136261;for(const character of value){result^=character.charCodeAt(0);result=Math.imul(result,16777619)}return result>>>0}
function layout(){const groups=new Map();for(const node of data.nodes){const cluster=(node.path||node.kind).split('/')[0]||node.kind;if(!groups.has(cluster))groups.set(cluster,[]);groups.get(cluster).push(node)}const width=canvas.clientWidth,height=canvas.clientHeight,entries=[...groups.entries()].sort((a,b)=>a[0].localeCompare(b[0]));entries.forEach((entry,groupIndex)=>{const nodes=entry[1],angle=2*Math.PI*groupIndex/Math.max(entries.length,1)-Math.PI/2,centerX=width/2+Math.cos(angle)*width*.27,centerY=height/2+Math.sin(angle)*height*.27;nodes.sort((a,b)=>a.key.localeCompare(b.key)).forEach((node,index)=>{const radius=18+Math.sqrt(index)*14,nodeAngle=(hash(node.key)%6283)/1000;positions.set(node.key,{x:centerX+Math.cos(nodeAngle)*radius,y:centerY+Math.sin(nodeAngle)*radius})})})}
function nodeVisible(node){const query=search.value.trim().toLowerCase();return(!kind.value||node.kind===kind.value)&&(!query||`${node.name} ${node.path||''} ${node.kind}`.toLowerCase().includes(query))}
function transformed(point){return{x:point.x*scale+offsetX,y:point.y*scale+offsetY}}
function draw(){if(!canvas.clientWidth)return;ctx.clearRect(0,0,canvas.clientWidth,canvas.clientHeight);const shown=new Set(data.nodes.filter(nodeVisible).map(node=>node.key));ctx.save();ctx.strokeStyle='#53627e';ctx.globalAlpha=.42;for(const edge of data.edges){if((edgeKind.value&&edge.kind!==edgeKind.value)||!shown.has(edge.source)||!shown.has(edge.target))continue;const first=transformed(positions.get(edge.source)),second=transformed(positions.get(edge.target));ctx.beginPath();ctx.moveTo(first.x,first.y);ctx.lineTo(second.x,second.y);ctx.stroke()}ctx.globalAlpha=1;for(const node of data.nodes){if(!shown.has(node.key))continue;const point=transformed(positions.get(node.key)),nodeFindings=data.findings.filter(finding=>finding.node_key===node.key);ctx.beginPath();ctx.arc(point.x,point.y,node.key===selected?8:5,0,Math.PI*2);ctx.fillStyle=colors.get(node.kind);ctx.fill();if(nodeFindings.length){ctx.beginPath();ctx.arc(point.x,point.y,10,0,Math.PI*2);ctx.strokeStyle=nodeFindings.some(f=>f.severity==='critical')?'#ff5d76':nodeFindings.some(f=>f.severity==='high')?'#ffad5c':'#f6d365';ctx.lineWidth=2;ctx.stroke();ctx.lineWidth=1}}ctx.restore();byId('summary').textContent=`${shown.size}/${data.totals.nodes} artifacts · ${data.totals.edges} relationships${data.truncated?' · bounded view':''}`;buildNodeTable()}
function resize(){if(!canvas.clientWidth)return;const ratio=devicePixelRatio||1;canvas.width=canvas.clientWidth*ratio;canvas.height=canvas.clientHeight*ratio;ctx.setTransform(ratio,0,0,ratio,0,0);layout();draw()}
function addBadge(value,parent){const badge=document.createElement('span');badge.className='badge';badge.textContent=value;parent.append(badge)}
function selectNode(key){selected=key;const node=data.nodes.find(item=>item.key===key);if(!node)return;detail.replaceChildren();const heading=document.createElement('h2');heading.textContent=node.name;detail.append(heading);[node.kind,node.path,node.language].filter(Boolean).forEach(value=>addBadge(value,detail));const related=data.edges.filter(edge=>edge.source===key||edge.target===key);const note=document.createElement('p');note.className='muted';note.textContent=`${related.length} relationships · ${data.findings.filter(item=>item.node_key===key).length} open findings`;detail.append(note);draw()}
function buildNodeTable(){table.replaceChildren();for(const node of data.nodes.filter(nodeVisible).slice(0,500)){const row=document.createElement('tr'),name=document.createElement('td'),type=document.createElement('td'),button=document.createElement('button');button.type='button';button.textContent=node.name;button.addEventListener('click',()=>selectNode(node.key));name.append(button);type.textContent=node.kind;row.append(name,type);table.append(row)}}
canvas.addEventListener('click',event=>{let nearest=null,distance=14;for(const node of data.nodes.filter(nodeVisible)){const point=transformed(positions.get(node.key)),candidate=Math.hypot(event.offsetX-point.x,event.offsetY-point.y);if(candidate<distance){nearest=node.key;distance=candidate}}if(nearest)selectNode(nearest)});canvas.addEventListener('wheel',event=>{event.preventDefault();scale=Math.max(.25,Math.min(4,scale*(event.deltaY<0?1.1:.9)));draw()},{passive:false});canvas.addEventListener('pointerdown',event=>{drag={x:event.clientX,y:event.clientY,offsetX,offsetY};canvas.setPointerCapture(event.pointerId)});canvas.addEventListener('pointermove',event=>{if(!drag)return;offsetX=drag.offsetX+event.clientX-drag.x;offsetY=drag.offsetY+event.clientY-drag.y;draw()});canvas.addEventListener('pointerup',()=>drag=null);for(const control of [search,kind,edgeKind])control.addEventListener(control===search?'input':'change',draw);byId('reset').addEventListener('click',()=>{scale=1;offsetX=0;offsetY=0;selected=null;search.value='';kind.value='';edgeKind.value='';draw()});
function empty(parent,text){const item=document.createElement('div');item.className='empty';item.textContent=text;parent.append(item)}
const snapshotSelect=byId('snapshot'),snapshotDetail=byId('snapshotDetail');if(data.snapshots.length){for(const snapshot of data.snapshots){const option=document.createElement('option');option.value=snapshot.id;option.textContent=`#${snapshot.sequence} · ${snapshot.label||snapshot.created_at}`;snapshotSelect.append(option)}const showSnapshot=()=>{const item=data.snapshots.find(value=>value.id===snapshotSelect.value);snapshotDetail.replaceChildren();if(!item)return;const title=document.createElement('strong');title.textContent=item.label||`Snapshot ${item.sequence}`;const note=document.createElement('p');note.className='muted';note.textContent=`${item.created_at} · ${item.inventory_mode} · ${item.node_count} nodes · ${item.edge_count} edges · ${item.finding_count} findings`;snapshotDetail.append(title,note)};snapshotSelect.addEventListener('change',showSnapshot);showSnapshot()}else{const option=document.createElement('option');option.textContent='No snapshots';snapshotSelect.append(option);snapshotSelect.disabled=true;empty(snapshotDetail,'Build the brain again after a change to create snapshot history.')}
const changeBox=byId('changes'),counts=data.changes.counts;byId('changeSummary').textContent=`${counts.added} added · ${counts.removed} removed · ${counts.modified} modified${data.changes.truncated?' · bounded list':''}`;if(data.changes.nodes.length){const wrap=document.createElement('div');wrap.className='table-wrap';const tableElement=document.createElement('table'),head=document.createElement('thead'),body=document.createElement('tbody');head.innerHTML='<tr><th>Change</th><th>Artifact</th><th>Kind</th><th>Path</th></tr>';for(const item of data.changes.nodes){const row=document.createElement('tr');for(const value of [item.change,item.name,item.kind,item.path||'']){const cell=document.createElement('td');cell.textContent=value;if(value===item.change){cell.className='change';cell.dataset.change=item.change}row.append(cell)}body.append(row)}tableElement.append(head,body);wrap.append(tableElement);changeBox.append(wrap)}else empty(changeBox,'No two distinct snapshots are available for comparison yet.');
const findingBox=byId('findings'),severity=byId('severity'),lens=byId('lens');[...new Set(data.findings.map(item=>item.lens))].sort().forEach(value=>{const option=document.createElement('option');option.value=value;option.textContent=value;lens.append(option)});function renderFindings(){findingBox.replaceChildren();const matches=data.findings.filter(item=>(!severity.value||item.severity===severity.value)&&(!lens.value||item.lens===lens.value));byId('findingSummary').textContent=`${matches.length}/${data.findings.length} open findings`;for(const finding of matches){const card=document.createElement('article');card.className='finding';card.dataset.severity=finding.severity;const title=document.createElement('strong');title.textContent=`${finding.severity.toUpperCase()} · ${finding.title}`;const description=document.createElement('p');description.textContent=finding.description;const remediation=document.createElement('p');remediation.className='muted';remediation.textContent=`Suggested: ${finding.remediation}`;card.append(title,description,remediation);findingBox.append(card)}if(!matches.length)empty(findingBox,'No findings match these filters.')}severity.addEventListener('change',renderFindings);lens.addEventListener('change',renderFindings);renderFindings();
const recovery=byId('recovery'),recoveryNodes=data.nodes.filter(item=>['data-store','service','rehearsal'].includes(item.kind)),recoveryFindings=data.findings.filter(item=>item.lens==='reliability-recovery');for(const [title,value,note] of [['Recovery artifacts',recoveryNodes.length,'Data stores, services, and rehearsal definitions'],['Recovery findings',recoveryFindings.length,'Open reliability and recovery review items'],['Recorded drills',data.runs.length,'Share-safe restore rehearsal results']]){const card=document.createElement('article');card.className='card';const heading=document.createElement('h3');heading.textContent=title;const count=document.createElement('strong');count.textContent=String(value);const description=document.createElement('p');description.className='muted';description.textContent=note;card.append(heading,count,description);recovery.append(card)}
const evidence=byId('evidence');for(const [title,items,formatter] of [['Memories',data.memories,item=>`${item.kind}: ${item.title}`],['Rehearsal runs',data.runs,item=>`${item.recipe}: ${item.status} · cleanup ${item.cleanup}`],['Imported evidence',data.imports,item=>`${item.kind}: ${item.source_name}`]]){const card=document.createElement('article');card.className='card';const heading=document.createElement('h3');heading.textContent=`${title} (${items.length})`;card.append(heading);if(items.length){const list=document.createElement('ul');for(const item of items.slice(0,20)){const entry=document.createElement('li');entry.textContent=formatter(item);list.append(entry)}card.append(list)}else{const note=document.createElement('p');note.className='muted';note.textContent='None recorded.';card.append(note)}evidence.append(card)}
const timeline=byId('timeline'),events=[];data.snapshots.forEach(item=>events.push({time:item.created_at,title:`Snapshot ${item.sequence}`,detail:item.label||`${item.node_count} nodes`}));data.memories.forEach(item=>events.push({time:item.created_at,title:`Memory · ${item.kind}`,detail:item.title}));data.runs.forEach(item=>events.push({time:item.started_at||item.finished_at,title:'Recovery rehearsal',detail:`${item.recipe}: ${item.status}`}));data.imports.forEach(item=>events.push({time:item.imported_at,title:`Imported · ${item.kind}`,detail:item.source_name}));events.sort((a,b)=>String(b.time||'').localeCompare(String(a.time||'')));for(const event of events.slice(0,300)){const card=document.createElement('article');card.className='timeline-item';const time=document.createElement('time');time.textContent=event.time||'time unavailable';const title=document.createElement('strong');title.textContent=event.title;const detail=document.createElement('div');detail.textContent=event.detail;card.append(time,title,detail);timeline.append(card)}if(!events.length)empty(timeline,'No historical evidence has been recorded yet.');
addEventListener('resize',resize);resize();
</script>
</body></html>
"""


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
