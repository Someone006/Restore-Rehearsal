# Minimal synthetic rehearsal

This fixture restores the text `known-good` into an isolated named volume, checks it with
Alpine 3.20, recreates the service with Alpine 3.21, and checks the restored data again.
It contains no credentials or real backup data.

From the repository root:

```console
rehearse validate examples/minimal/rehearse.yaml
rehearse run examples/minimal/rehearse.yaml --report minimal-report.json
```

The run pulls the two public images if they are not already available. The report is created
with private file permissions, and the rehearsal containers, network, and volume are removed
after either success or failure.
