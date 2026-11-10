# Managed-service boundary

Core Rehearse consumes user-provided local logical exports. It does not create snapshots, download
exports, assume roles, rotate credentials, or call AWS, Google Cloud, Azure, or SaaS APIs.

If future trusted plugins add those operations, treat them as separate user-authorized network and
credential workflows. Never store provider credentials in recipes, reports, brains, or memories.
