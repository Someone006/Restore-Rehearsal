# Security policy

## Reporting a vulnerability

Please use the repository's **private vulnerability reporting** feature under the Security tab. Do
not open a public issue containing an exploit, credential, backup sample, private Compose file, or
personal data.

Include the affected version, a minimal synthetic reproduction, impact, and suggested mitigation.
Maintainers will acknowledge a report when available and coordinate disclosure after a fix.

## Scope

Security issues include production-resource reachability, cleanup escaping the exact run label,
backup mutation, unsafe host mounts, report/console secret disclosure, and command injection in the
host process. A recipe intentionally executing a command inside a rehearsal container is expected
behavior; recipes are trusted code.

Only the latest release receives security fixes while the project is in alpha.
