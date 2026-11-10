# Universal inspection

Universal Inspection gives people and AI agents one evidence format for websites, native/mobile
apps, CLIs/TUIs, APIs, source code, documentation, data, and mixed projects. It is deliberately
split into two parts: Rehearse creates and validates a local plan/report, while an explicitly
chosen browser, desktop, emulator, terminal, or test tool performs the authorized observation.

The Rehearse CLI does not open URLs, launch applications, run project code, or take screenshots by
itself. This keeps a plan useful on machines and agents with different capabilities without
silently granting execution or network access.

## Create and validate a plan

```console
rehearse inspect plan ./app --surface mixed --output inspection-plan.json --json
rehearse inspect validate inspection-plan.json --json
```

`--surface` accepts `auto`, `web`, `native`, `cli`, `api`, `code`, `docs`, `data`, or `mixed`.
Automatic classification uses only the target descriptor; it does not contact a URL or inspect a
file body. Web targets retain only scheme, host, port, and path: query strings, fragments, and
embedded credentials are refused or removed.

Every plan records:

- a content-minimized target descriptor and surface;
- bounded checks appropriate to that surface;
- whether each check normally needs visual evidence; and
- fixed guardrails forbidding automatic execution/network use and requiring private evidence plus
  outbound consent.

Plans are private mode-`0600` JSON files, are written atomically, and never overwrite an existing
path.

## Perform the inspection

Use the bundled `universal-inspector` Agent Skill with the local tools available to the agent. The
workflow pairs semantic evidence with pixels:

1. Define the user-visible claim and expected observation.
2. Record target/version, accessible structure or machine result, dimensions, state, and time.
3. Use the smallest controlled interaction needed to verify the claim.
4. Cover relevant loading, empty, success, validation, error, permission, recovery, and
   destructive-confirmation states.
5. Capture a focused screenshot or recording only after the state is visible.
6. Check keyboard/focus, accessible names and states, contrast/reflow, privacy, and recovery as
   appropriate.
7. Separate observed facts, interpretation, limitations, and untested states.

For a website this can mean accessibility-tree inspection, console/request summaries, desktop and
mobile screenshots, keyboard navigation, and focused interaction tests. For a native app it can
mean window state, scaling, menus, system dialogs, offline behavior, and screenshots from a
disposable profile. CLI/API/code/docs/data inspections use exit codes, schemas, tests, structure,
lineage, and bounded synthetic samples; a decorative screenshot is never treated as functional
proof.

## Reports and local evidence

The Python API can build a validated report with `build_inspection_report`. A report contains
check results, issues, limitations, and optional evidence records. Supported evidence kinds include
accessibility state, artifact metadata, console/network summaries, screenshots, test results,
text observations, and video.

File-backed evidence uses a relative path and SHA-256 digest. Keep the report and evidence in one
dedicated directory, then verify them together:

```console
rehearse inspect validate inspection-report.json --verify-evidence --json
```

Verification rejects absolute/traversal paths, symlinks, hard-linked files, directory escape,
oversized files, missing evidence, and digest changes. Metadata-only evidence is counted but cannot
be cryptographically verified. The report validator also rejects unknown fields, invalid status
transitions, missing evidence references, unsafe control text, and common credential patterns.

The CLI currently validates reports produced by an agent or integrator; it does not include an
interactive report editor. See `restore_rehearsal.inspection` for the stable alpha constructors and
schemas.

## Privacy and authorization

Inspection is read-only by default. Do not submit, purchase, publish, delete, deploy, message,
change access, or mutate production state unless the user explicitly requested that exact action.
Use disposable accounts, profiles, fixtures, and environments for stateful tests.

Screenshots, recordings, accessibility trees, terminals, logs, API payloads, and documents can
contain private tabs, notifications, faces, paths, customer records, or tokens. Minimize the
visible surface, crop/redact locally, and do not commit raw captures by default.

API keys, credentials, tokens, private keys, and backup contents must never be uploaded. Before
sending any other screenshot, report, source, project, personal, or customer data to an online
service, name the destination and exact scope and obtain the user's explicit approval. Local-only
inspection requires no outbound-data approval.

An inspection pass is not a security assessment, WCAG certification, legal conclusion,
performance guarantee, or proof about states that were not observed.
