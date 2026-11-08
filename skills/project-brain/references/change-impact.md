# Change Impact and Contracts

Lens ID: `change-impact`

Trace both incoming and outgoing relationships for the artifact being changed. Look for API/schema/
config consumers, migrations, dependent services, and tests. Flag large blast radius, missing
compatibility evidence, and migration-order risk.

Recommend contract fixtures, affected-test selection, compatibility checks, or explicit migration
sequencing. Do not infer a breaking change solely from a filename or dependency count.
