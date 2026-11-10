# Support and version policy

Rehearse is maintained as an Apache-2.0 community project. There is no paid SLA and no warranty.
Issues and pull requests are handled on a best-effort basis; never use a public issue to disclose a
credential, private project, customer data, or an unpatched vulnerability.

## Alpha releases

Until `1.0.0`, command details and alpha schemas may change between minor versions. Patch releases
preserve the documented recipe/report schema and correct defects without intentionally removing a
feature. Each release notes any migration requirement. Users should pin a tag or exact package
version and test upgrades on non-production inputs.

The project aims to support:

- the latest release for security-relevant fixes while the project is in alpha;
- Python 3.11 and 3.12 for the current `0.3` line;
- Linux for Docker restore drills; and
- brain-only installed-artifact smoke tests on Linux, macOS, and Windows.

Older versions may continue to work but are not exercised by current CI. Python or platform support
is removed only in a minor release and documented in the changelog and compatibility matrix.

## Security reports

Follow [SECURITY.md](../SECURITY.md). Include the affected version, a minimal synthetic reproduction,
and impact. Do not attach real backups, tokens, customer data, private source, or screenshots that
contain personal information. Maintainers may request a safer reproduction before investigating.

## Scope boundaries

Automated findings are issue-spotting evidence, not legal, security, accessibility, performance, or
disaster-recovery certification. Third-party plugins execute with the user's local privileges and
are outside the core trust boundary. Provider credentials and live managed-service actions are not
part of the default local workflow.
