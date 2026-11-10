# Synthetic project and recovery templates

These fixtures contain no credentials, personal information, production endpoints, or real backup
data. They are starting points for adapter detection and evidence import—not executable production
restore procedures.

| Directory | Demonstrates |
| --- | --- |
| `postgres/` | User-supplied PostgreSQL logical export metadata |
| `mysql/` | User-supplied MySQL/MariaDB logical export metadata |
| `sqlite/` | SQLite local-file handoff and integrity-check requirements |
| `mongodb/` | MongoDB archive/dump handoff |
| `object-export/` | Local object-storage export manifest |
| `web-api/` | Stateless OpenAPI evidence without a database |

Use adapter detection on a dedicated local fixture/project directory. Review every proposed engine,
image, restore command, database name, and application-specific check before generating or running a
rehearsal. Rehearse never turns these manifests into a live cloud request and never asks for an API
key. Provider-native snapshots must first be exported locally in a supported logical format.
