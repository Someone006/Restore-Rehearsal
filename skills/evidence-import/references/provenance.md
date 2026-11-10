# Evidence provenance

Record source basename, SHA-256, detected kind, importer ID/version, import time, normalized counts,
and truncation. Keep raw artifact bytes outside the brain under the user's retention policy.

Imported findings remain tool output requiring human review. SBOM presence is not license
clearance; a SARIF result is not a vulnerability determination; a test report is not runtime proof.
