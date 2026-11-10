# Project detection

Use bounded, non-executing evidence: manifests, lockfiles, Compose/Kubernetes/Terraform files,
database extensions, CI workflows, test directories, and existing Rehearse artifacts. Prefer an
explicit declaration over a filename heuristic. Record confidence and conflicting signals.

Detection may recommend profiles, importers, and adapter templates. It must never discover secrets
by reading sensitive paths or contact a cloud provider. Missing evidence means unknown, not absent.
