"""SQLite format constants and schema."""

APPLICATION_ID = 0x52524252  # RRBR
SCHEMA_VERSION = 2

SCHEMA_V1_SQL = """
PRAGMA journal_mode=DELETE;
PRAGMA synchronous=FULL;
PRAGMA foreign_keys=ON;
PRAGMA trusted_schema=OFF;
PRAGMA secure_delete=ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY CHECK(typeof(key) = 'text' AND length(key) BETWEEN 1 AND 100),
    value TEXT NOT NULL CHECK(typeof(value) = 'text' AND length(value) <= 2000)
) WITHOUT ROWID;

CREATE TABLE nodes (
    key TEXT PRIMARY KEY CHECK(typeof(key) = 'text' AND length(key) BETWEEN 1 AND 600),
    kind TEXT NOT NULL CHECK(typeof(kind) = 'text' AND length(kind) BETWEEN 1 AND 64),
    name TEXT NOT NULL CHECK(typeof(name) = 'text' AND length(name) BETWEEN 1 AND 300),
    path TEXT CHECK(path IS NULL OR (typeof(path) = 'text' AND length(path) <= 1000)),
    line INTEGER CHECK(line IS NULL OR (typeof(line) = 'integer' AND line > 0)),
    language TEXT CHECK(language IS NULL OR (typeof(language) = 'text' AND length(language) <= 100)),
    sha256 TEXT CHECK(sha256 IS NULL OR (typeof(sha256) = 'text' AND length(sha256) = 64)),
    properties_json TEXT NOT NULL CHECK(typeof(properties_json) = 'text' AND length(properties_json) <= 16384)
) WITHOUT ROWID;

CREATE TABLE edges (
    source_key TEXT NOT NULL REFERENCES nodes(key) ON DELETE CASCADE,
    target_key TEXT NOT NULL REFERENCES nodes(key) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(typeof(kind) = 'text' AND length(kind) BETWEEN 1 AND 64),
    provenance TEXT NOT NULL CHECK(typeof(provenance) = 'text' AND length(provenance) <= 32),
    evidence_path TEXT NOT NULL DEFAULT '' CHECK(typeof(evidence_path) = 'text' AND length(evidence_path) <= 1000),
    evidence_line INTEGER NOT NULL DEFAULT 0 CHECK(typeof(evidence_line) = 'integer' AND evidence_line >= 0),
    details_json TEXT NOT NULL CHECK(typeof(details_json) = 'text' AND length(details_json) <= 16384),
    PRIMARY KEY (
        source_key, target_key, kind, provenance, evidence_path, evidence_line
    )
) WITHOUT ROWID;

CREATE TABLE findings (
    fingerprint TEXT PRIMARY KEY CHECK(typeof(fingerprint) = 'text' AND length(fingerprint) = 64),
    lens TEXT NOT NULL CHECK(typeof(lens) = 'text' AND length(lens) BETWEEN 1 AND 64),
    rule TEXT NOT NULL CHECK(typeof(rule) = 'text' AND length(rule) BETWEEN 1 AND 64),
    severity TEXT NOT NULL CHECK(severity IN ('low', 'medium', 'high', 'critical')),
    title TEXT NOT NULL CHECK(typeof(title) = 'text' AND length(title) BETWEEN 1 AND 300),
    description TEXT NOT NULL CHECK(typeof(description) = 'text' AND length(description) BETWEEN 1 AND 2000),
    remediation TEXT NOT NULL CHECK(typeof(remediation) = 'text' AND length(remediation) BETWEEN 1 AND 2000),
    confidence TEXT NOT NULL CHECK(confidence IN ('extracted', 'rule-derived', 'heuristic')),
    node_key TEXT REFERENCES nodes(key) ON DELETE SET NULL,
    evidence_path TEXT CHECK(evidence_path IS NULL OR (typeof(evidence_path) = 'text' AND length(evidence_path) <= 1000)),
    evidence_line INTEGER CHECK(evidence_line IS NULL OR (typeof(evidence_line) = 'integer' AND evidence_line > 0)),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'accepted', 'resolved', 'dismissed')),
    first_seen TEXT NOT NULL CHECK(typeof(first_seen) = 'text' AND length(first_seen) <= 64),
    last_seen TEXT NOT NULL CHECK(typeof(last_seen) = 'text' AND length(last_seen) <= 64)
) WITHOUT ROWID;

CREATE TABLE lenses (
    id TEXT PRIMARY KEY CHECK(typeof(id) = 'text' AND length(id) BETWEEN 1 AND 64),
    name TEXT NOT NULL CHECK(typeof(name) = 'text' AND length(name) BETWEEN 1 AND 200),
    category TEXT NOT NULL CHECK(typeof(category) = 'text' AND length(category) BETWEEN 1 AND 64),
    description TEXT NOT NULL CHECK(typeof(description) = 'text' AND length(description) BETWEEN 1 AND 2000),
    reference_url TEXT CHECK(reference_url IS NULL OR (typeof(reference_url) = 'text' AND length(reference_url) <= 500))
) WITHOUT ROWID;

CREATE TABLE memories (
    id TEXT PRIMARY KEY CHECK(typeof(id) = 'text' AND length(id) = 24),
    kind TEXT NOT NULL CHECK(kind IN ('decision', 'attempt', 'failure', 'fix', 'note', 'risk-acceptance')),
    title TEXT NOT NULL CHECK(typeof(title) = 'text' AND length(title) BETWEEN 1 AND 200),
    body TEXT NOT NULL CHECK(typeof(body) = 'text' AND length(CAST(body AS BLOB)) BETWEEN 1 AND 16384),
    path TEXT CHECK(path IS NULL OR (typeof(path) = 'text' AND length(path) <= 500)),
    status TEXT NOT NULL CHECK(status IN ('active', 'superseded', 'deleted')),
    supersedes TEXT CHECK(supersedes IS NULL OR (typeof(supersedes) = 'text' AND length(supersedes) = 24)),
    created_at TEXT NOT NULL CHECK(typeof(created_at) = 'text' AND length(created_at) <= 64),
    content_sha256 TEXT NOT NULL CHECK(typeof(content_sha256) = 'text' AND length(content_sha256) = 64)
) WITHOUT ROWID;

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY CHECK(typeof(run_id) = 'text' AND length(run_id) = 16),
    recipe TEXT NOT NULL CHECK(typeof(recipe) = 'text' AND length(recipe) BETWEEN 1 AND 100),
    status TEXT NOT NULL CHECK(typeof(status) = 'text' AND length(status) BETWEEN 1 AND 20),
    cleanup TEXT NOT NULL CHECK(typeof(cleanup) = 'text' AND length(cleanup) BETWEEN 1 AND 20),
    started_at TEXT CHECK(started_at IS NULL OR (typeof(started_at) = 'text' AND length(started_at) <= 64)),
    finished_at TEXT CHECK(finished_at IS NULL OR (typeof(finished_at) = 'text' AND length(finished_at) <= 64)),
    report_sha256 TEXT NOT NULL CHECK(typeof(report_sha256) = 'text' AND length(report_sha256) = 64),
    summary_json TEXT NOT NULL CHECK(typeof(summary_json) = 'text' AND length(summary_json) <= 131072)
) WITHOUT ROWID;

CREATE INDEX nodes_kind_idx ON nodes(kind);
CREATE INDEX nodes_path_idx ON nodes(path);
CREATE INDEX edges_target_idx ON edges(target_key);
CREATE INDEX findings_lens_idx ON findings(lens, severity, status);
CREATE INDEX memories_kind_idx ON memories(kind, created_at);
"""

SCHEMA_SQL = (
    SCHEMA_V1_SQL
    + """

CREATE TABLE snapshots (
    id TEXT PRIMARY KEY CHECK(typeof(id) = 'text' AND length(id) = 24),
    sequence INTEGER NOT NULL UNIQUE CHECK(typeof(sequence) = 'integer' AND sequence > 0),
    created_at TEXT NOT NULL CHECK(typeof(created_at) = 'text' AND length(created_at) <= 64),
    label TEXT CHECK(label IS NULL OR (typeof(label) = 'text' AND length(label) BETWEEN 1 AND 200)),
    semantic_digest TEXT NOT NULL CHECK(typeof(semantic_digest) = 'text' AND length(semantic_digest) = 64),
    git_commit TEXT NOT NULL CHECK(typeof(git_commit) = 'text' AND length(git_commit) BETWEEN 1 AND 64),
    inventory_mode TEXT NOT NULL CHECK(inventory_mode IN ('git', 'filesystem', 'unknown')),
    node_count INTEGER NOT NULL CHECK(typeof(node_count) = 'integer' AND node_count >= 0),
    edge_count INTEGER NOT NULL CHECK(typeof(edge_count) = 'integer' AND edge_count >= 0),
    finding_count INTEGER NOT NULL CHECK(typeof(finding_count) = 'integer' AND finding_count >= 0)
) WITHOUT ROWID;

CREATE TABLE snapshot_nodes (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    node_key TEXT NOT NULL CHECK(typeof(node_key) = 'text' AND length(node_key) BETWEEN 1 AND 600),
    kind TEXT NOT NULL CHECK(typeof(kind) = 'text' AND length(kind) BETWEEN 1 AND 64),
    name TEXT NOT NULL CHECK(typeof(name) = 'text' AND length(name) BETWEEN 1 AND 300),
    path TEXT CHECK(path IS NULL OR (typeof(path) = 'text' AND length(path) <= 1000)),
    line INTEGER CHECK(line IS NULL OR (typeof(line) = 'integer' AND line > 0)),
    language TEXT CHECK(language IS NULL OR (typeof(language) = 'text' AND length(language) <= 100)),
    sha256 TEXT CHECK(sha256 IS NULL OR (typeof(sha256) = 'text' AND length(sha256) = 64)),
    properties_json TEXT NOT NULL CHECK(typeof(properties_json) = 'text' AND length(properties_json) <= 16384),
    content_digest TEXT NOT NULL CHECK(typeof(content_digest) = 'text' AND length(content_digest) = 64),
    PRIMARY KEY (snapshot_id, node_key)
) WITHOUT ROWID;

CREATE TABLE snapshot_edges (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    source_key TEXT NOT NULL CHECK(typeof(source_key) = 'text' AND length(source_key) BETWEEN 1 AND 600),
    target_key TEXT NOT NULL CHECK(typeof(target_key) = 'text' AND length(target_key) BETWEEN 1 AND 600),
    kind TEXT NOT NULL CHECK(typeof(kind) = 'text' AND length(kind) BETWEEN 1 AND 64),
    provenance TEXT NOT NULL CHECK(typeof(provenance) = 'text' AND length(provenance) <= 32),
    evidence_path TEXT NOT NULL DEFAULT '' CHECK(typeof(evidence_path) = 'text' AND length(evidence_path) <= 1000),
    evidence_line INTEGER NOT NULL DEFAULT 0 CHECK(typeof(evidence_line) = 'integer' AND evidence_line >= 0),
    details_json TEXT NOT NULL CHECK(typeof(details_json) = 'text' AND length(details_json) <= 16384),
    content_digest TEXT NOT NULL CHECK(typeof(content_digest) = 'text' AND length(content_digest) = 64),
    PRIMARY KEY (
        snapshot_id, source_key, target_key, kind, provenance, evidence_path, evidence_line
    ),
    FOREIGN KEY (snapshot_id, source_key)
        REFERENCES snapshot_nodes(snapshot_id, node_key) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id, target_key)
        REFERENCES snapshot_nodes(snapshot_id, node_key) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE snapshot_findings (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL CHECK(typeof(fingerprint) = 'text' AND length(fingerprint) = 64),
    lens TEXT NOT NULL CHECK(typeof(lens) = 'text' AND length(lens) BETWEEN 1 AND 64),
    rule TEXT NOT NULL CHECK(typeof(rule) = 'text' AND length(rule) BETWEEN 1 AND 64),
    severity TEXT NOT NULL CHECK(severity IN ('low', 'medium', 'high', 'critical')),
    title TEXT NOT NULL CHECK(typeof(title) = 'text' AND length(title) BETWEEN 1 AND 300),
    description TEXT NOT NULL CHECK(typeof(description) = 'text' AND length(description) BETWEEN 1 AND 2000),
    remediation TEXT NOT NULL CHECK(typeof(remediation) = 'text' AND length(remediation) BETWEEN 1 AND 2000),
    confidence TEXT NOT NULL CHECK(confidence IN ('extracted', 'rule-derived', 'heuristic')),
    node_key TEXT CHECK(node_key IS NULL OR (typeof(node_key) = 'text' AND length(node_key) <= 600)),
    evidence_path TEXT CHECK(evidence_path IS NULL OR (typeof(evidence_path) = 'text' AND length(evidence_path) <= 1000)),
    evidence_line INTEGER CHECK(evidence_line IS NULL OR (typeof(evidence_line) = 'integer' AND evidence_line > 0)),
    status TEXT NOT NULL CHECK(status IN ('open', 'accepted', 'resolved', 'dismissed')),
    content_digest TEXT NOT NULL CHECK(typeof(content_digest) = 'text' AND length(content_digest) = 64),
    PRIMARY KEY (snapshot_id, fingerprint)
) WITHOUT ROWID;

CREATE TABLE imports (
    id TEXT PRIMARY KEY CHECK(typeof(id) = 'text' AND length(id) = 24),
    kind TEXT NOT NULL CHECK(typeof(kind) = 'text' AND length(kind) BETWEEN 1 AND 64),
    source_name TEXT NOT NULL CHECK(typeof(source_name) = 'text' AND length(source_name) BETWEEN 1 AND 200),
    sha256 TEXT NOT NULL CHECK(typeof(sha256) = 'text' AND length(sha256) = 64),
    importer_id TEXT NOT NULL CHECK(typeof(importer_id) = 'text' AND length(importer_id) BETWEEN 1 AND 64),
    importer_version TEXT NOT NULL CHECK(typeof(importer_version) = 'text' AND length(importer_version) BETWEEN 1 AND 32),
    imported_at TEXT NOT NULL CHECK(typeof(imported_at) = 'text' AND length(imported_at) <= 64),
    summary_json TEXT NOT NULL CHECK(typeof(summary_json) = 'text' AND length(summary_json) <= 131072)
) WITHOUT ROWID;

CREATE TABLE import_fragments (
    import_id TEXT NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK(typeof(ordinal) = 'integer' AND ordinal >= 0),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text' AND length(payload_json) <= 131072),
    payload_sha256 TEXT NOT NULL CHECK(typeof(payload_sha256) = 'text' AND length(payload_sha256) = 64),
    PRIMARY KEY (import_id, ordinal)
) WITHOUT ROWID;

CREATE INDEX snapshots_sequence_idx ON snapshots(sequence DESC);
CREATE INDEX snapshot_nodes_kind_idx ON snapshot_nodes(snapshot_id, kind);
CREATE INDEX snapshot_edges_source_idx ON snapshot_edges(snapshot_id, source_key);
CREATE INDEX snapshot_edges_target_idx ON snapshot_edges(snapshot_id, target_key);
CREATE INDEX snapshot_findings_lens_idx ON snapshot_findings(snapshot_id, lens, severity);
CREATE INDEX imports_kind_idx ON imports(kind, imported_at);
"""
)

EXPECTED_TABLES = {
    "metadata",
    "nodes",
    "edges",
    "findings",
    "lenses",
    "memories",
    "runs",
    "snapshots",
    "snapshot_nodes",
    "snapshot_edges",
    "snapshot_findings",
    "imports",
    "import_fragments",
}
