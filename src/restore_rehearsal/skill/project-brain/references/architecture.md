# Architecture Map

Lens ID: `architecture`

Use manifests, imports, packages, Compose services, workflows, and architecture records to identify
components and dependency direction. Prefer extracted edges with file/line evidence; label any
inference. Review high fan-in, hidden coupling, orphan components, undocumented boundaries, and
single points of failure.

Propose architecture assertions and focused tests rather than automatic refactors. A missing edge is
insufficient evidence, not proof of independence. See the evidence-backed
[Repository Intelligence Graph](https://arxiv.org/abs/2601.10112) approach.
