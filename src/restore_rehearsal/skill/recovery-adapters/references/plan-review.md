# Recovery plan review

Confirm engine and source version, target image digest, encoding, compression, roles/owners,
extensions, database selection, restore ordering, read-only mounts, resource limits, network
isolation, health readiness, integrity checks, application checks, upgrade step, and cleanup.

Prefer argument arrays and stdin/file mounts over interpolated shell commands. Never make a plan
pass by reusing an external production volume, network, bind, or credential.
