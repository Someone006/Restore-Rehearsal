---
name: rehearse-init
description: Initialize Rehearse in an existing local project by detecting its stack, previewing safe files, and adding Project Brain or restore-rehearsal configuration without overwriting work.
license: Apache-2.0
compatibility: Requires Python 3.11+ and the rehearse CLI.
metadata:
  version: "0.3.0"
---

# Rehearse Init

Use this skill when a user asks to set up Rehearse, Project Brain, recovery checks, or agent skills
in an existing project.

## Workflow

1. Work only inside the project root the user selected. Git is optional.
2. Run `rehearse init ROOT --dry-run --json` and review detected manifests, services, data stores,
   test commands, ignore rules, and proposed files. Follow [project detection](references/detection.md).
3. Explain uncertain detections. Never guess a production database, backup location, credential, or
   destructive command.
4. After the preview is acceptable, run `rehearse init ROOT`. It must create only absent files and
   report every skipped path. Follow [safe generated files](references/generated-files.md).
5. Validate generated recipes and build the initial brain. Treat generated content as a starting
   point that still needs project-owner review.

## Guardrails

- Never overwrite, merge, delete, or weaken an existing project file or ignore rule.
- Detection is static and local. Do not execute project code, package scripts, migrations, or
  production commands to improve a guess.
- Never place real credentials, customer data, backup contents, or absolute private paths in a
  generated file. Use documented placeholders.
- API keys, credentials, tokens, private keys, and backup contents must never be sent to an online
  service. Before sending any other project, report, personal, or customer data online, state the
  destination and scope and obtain explicit user approval. Purely local work needs no approval.
- Do not claim a detected stack is complete; name evidence and unresolved choices.
