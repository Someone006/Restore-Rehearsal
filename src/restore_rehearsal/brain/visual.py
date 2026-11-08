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
    html = HTML.replace("__BRAIN_DATA__", encoded).replace("__CSP_NONCE__", nonce)
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
    return {
        "nodes": nodes,
        "edges": edges,
        "findings": findings,
        "memories": memories,
        "runs": runs,
        "totals": {"nodes": total_nodes, "edges": total_edges},
        "truncated": total_nodes > len(nodes) or total_edges > len(edges),
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


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
