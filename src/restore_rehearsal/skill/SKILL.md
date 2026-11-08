---
name: restore-rehearsal
description: Safely validate and run disposable Docker Compose backup restore and candidate-upgrade drills with the Rehearse CLI.
license: Apache-2.0
compatibility: Requires Python 3.11+, Docker Compose v2, and the rehearse CLI.
metadata:
  version: "0.1.0"
---

# Restore Rehearsal

Use this skill when a user wants to test whether a backup restores, rehearse a stateful
Docker Compose upgrade, or add a recovery drill to CI. Rehearse creates disposable resources;
it does not back up, update, deploy, or roll back production.

## Safe workflow

1. Read the project's `rehearse.yaml`, Compose files, and declared checks. Treat the recipe as
   trusted executable instructions because its command arrays run inside rehearsal containers.
2. Run `rehearse validate PATH/TO/rehearse.yaml` first. Explain every refusal or warning. Do not
   weaken a safety setting or alter a production Compose definition merely to make validation pass.
3. Confirm that the selected backup is the intended non-production input. Never print, upload,
   commit, or inspect backup contents unless the user explicitly asks.
4. Run `rehearse run PATH/TO/rehearse.yaml --report PATH/TO/report.json` only after validation.
   Choose a report path outside source control; Rehearse refuses to overwrite an existing file.
5. Report the restore, baseline, candidate, and cleanup statuses. A passed run proves only that
   the declared backup, images, procedure, and checks worked in that rehearsal environment.

## Guardrails

- Never add `allow_egress: true` unless the drill genuinely requires outbound access and the user
  accepts that boundary change.
- Never replace a rejected external volume or network with its production resource.
- Never publish raw Compose rendering, container output, environment values, backup paths, or
  credentials. Use the share-safe JSON report.
- Never apply the candidate images to production as part of this skill. Production rollout is a
  separate user-authorized action.
- If automatic cleanup fails, stop and report the `run_id`; do not run broad Docker deletion
  commands or delete resources by a partial name.

See the repository's `docs/recipe.md` and `docs/safety.md` for the current alpha schema and threat
boundary.
