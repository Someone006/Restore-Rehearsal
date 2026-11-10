---
name: universal-inspector
description: Inspect and verify websites, native or mobile apps, CLIs and TUIs, APIs, source code, documents, and data using screenshots, semantic checks, controlled interactions, and share-safe evidence.
license: Apache-2.0
compatibility: Works with any agent that can inspect local files; screenshots and interaction require an available browser, desktop, emulator, or terminal capture tool.
metadata:
  version: "0.3.0"
---

# Universal Inspector

Use this skill when a user asks to inspect, screenshot, test, compare, review, or visually verify any
digital project surface—not only a website.

## Evidence workflow

1. Classify each target as web, native/mobile, CLI/TUI, API, code, document, or data. Define the
   user-visible claim and observable acceptance criteria using [targets and evidence](references/targets-and-evidence.md).
2. Prefer a purpose-built local tool and read its instructions before interaction. Establish a
   disposable test account, profile, workspace, or fixture when state can change.
3. Capture semantic state before pixels: URL/window, accessible structure, response/status, command
   and exit code, relevant artifact hash, viewport/device/terminal dimensions, and time.
4. Exercise the smallest controlled interaction that can verify the claim. Cover loading, empty,
   success, validation, error, permission, recovery, and destructive-confirmation states when they
   exist.
5. Capture focused screenshots or recordings after the state is visible. Follow the target guide:
   [web](references/web-responsive.md), [native/mobile](references/native-mobile.md),
   [CLI/TUI](references/cli-terminal.md), [API](references/api-contracts.md), or
   [code/docs/data](references/code-docs-data.md).
6. Check accessibility and privacy with [the cross-surface checklist](references/accessibility-privacy.md).
7. Report expected versus observed behavior, reproducible steps, evidence metadata, confidence, and
   limitations. Store evidence locally and import only bounded, share-safe facts into Project Brain.

## Guardrails

- Inspection is read-only by default. Never submit, purchase, publish, delete, deploy, message,
  change access, or mutate production state unless the user explicitly requested that exact action.
- Do not execute untrusted project code merely to inspect it. Use declared test commands in an
  isolated environment only when execution is within the request.
- Screenshots and recordings may contain private tabs, notifications, faces, customer data, paths,
  or tokens. Crop or redact locally and never commit raw captures by default.
- API keys, credentials, tokens, private keys, and backup contents must never be sent to an online
  service. Before sending any other source, screenshot, report, project, personal, or customer data
  online, state the destination and scope and obtain explicit user approval. Purely local work
  needs no approval.
- A screenshot alone does not prove functionality, security, usability, performance, or WCAG
  conformance. Pair visual evidence with semantic output and clearly label untested states.
