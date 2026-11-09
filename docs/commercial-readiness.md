# Commercial use and production readiness

Apache-2.0 permits commercial use. The
[Apache Licensing FAQ](https://www.apache.org/foundation/license-faq.html) states that the Apache
Software Foundation does not distinguish personal, internal, or commercial use. Redistribution and
modification still require compliance with the license, notice, patent, and trademark terms. This
document is project guidance, not legal advice.

## Current recommendation

Rehearse is suitable for a commercial evaluation or advisory CI signal when it runs on an isolated
worker, uses reviewed recipes and non-production restore targets, pins the tool version, and has a
human interpret the result.

The current alpha must not be the sole production release gate, contractual disaster-recovery
control, accessibility certification, or legal/compliance determination. A successful run proves
only that the declared inputs and checks passed in that rehearsal environment.

## Before a production-critical pilot

1. Publish a compatibility matrix covering real PostgreSQL, MySQL/MariaDB, SQLite/file stores,
   compressed/large backups, dependent services, interruption, and multi-hop migrations.
2. Add a crash-resilient run journal plus ownership-checked status/cleanup recovery after hard kill,
   daemon loss, and host restart.
3. Obtain independent review of Docker-root assumptions, symlink/TOCTOU behavior, remote Docker
   contexts, parser exhaustion, cleanup selection, redaction, and malicious Compose inputs.
4. Stabilize recipe/report/brain compatibility and publish migration/deprecation policy with golden
   fixtures from every supported schema.
5. Record reproducible evidence: actual image digests, Docker/Compose/tool versions, backup and
   check hashes, timestamps, and explicit limits of what checks inspected.

## Supply-chain and project controls

[NIST SSDF SP 800-218](https://csrc.nist.gov/pubs/sp/800/218/final) recommends collecting and
sharing release provenance, including SBOM information. The
[OpenSSF OSPS Baseline](https://baseline.openssf.org/) provides maturity-based controls for secure
open-source projects. Before calling Rehearse stable, the project should have:

- protected primary branches with required CI and independent human review;
- signed release assets or a signed manifest, checksums, provenance, and CycloneDX/SPDX SBOMs;
- dependency vulnerability, secret, static-analysis, and license scanning;
- verified private vulnerability reporting, response targets, revocation/incident procedure, and a
  supported-version/end-of-support table;
- reproducible packaging and installed-artifact tests on every claimed platform; and
- contribution provenance plus third-party attribution/NOTICE inventory.

The current direct dependency licenses are listed in [the third-party inventory](../THIRD_PARTY.md).

## Project Brain in commercial repositories

The brain is local and content-minimizing by default, but its graph can still reveal architecture,
file names, dependencies, findings, and deliberately recorded decisions. Commercial users should:

- keep `.rrbrain` and exported HTML outside source control and customer deliverables;
- define retention, access, deletion, and approved-memory policies;
- never record secrets, personal data, contract terms, or raw customer content;
- never transmit API keys, credentials, tokens, private keys, or backup content to online services;
- require explicit user approval, with destination and scope, before transmitting any other project
  or personal/customer data to an online service;
- validate scanner limits and exclusions against project policy; and
- treat license/privacy/accessibility findings as review queues for qualified people—not conclusions.

The [W3C WCAG 2.2 Recommendation](https://www.w3.org/TR/WCAG22/) provides testable accessibility
criteria, but static Project Brain rules cover only a small subset. Likewise, automated license and
privacy artifact checks cannot determine jurisdiction-specific obligations.
