# Performance and scale checks

Project Brain enforces correctness and resource bounds before performance. The scanner has explicit
file, path-byte, per-file, total-read, directory, depth, and ignore-rule limits. Reaching a limit is
a refusal, not a partial-success claim.

Use the synthetic benchmark harness in [`benchmarks/`](../benchmarks/) for comparable local runs.
Recommended profiles are 1,000, 10,000, and 20,000 generated project files. Record:

- commit and package version;
- OS, CPU, memory, filesystem, Python version, and Git availability;
- first/second build time and semantic digest;
- brain size, graph counts, query latency, and peak child-process RSS; and
- whether Git or filesystem inventory mode was used.

Pull requests should gate deterministic output and hard limits, not absolute wall-clock time on
shared runners. A nightly or manually dispatched job may retain time series, but a regression needs
reproduction on comparable hardware before being treated as a defect.

`--files` counts every generated regular file, including the synthetic README and
`.rehearseignore`; the 20,000 profile exercises the scanner's exact file-count limit.
