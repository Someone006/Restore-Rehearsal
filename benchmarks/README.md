# Project Brain benchmarks

The benchmark suite generates only synthetic source files and records structural results separately
from timing. Run it from an editable checkout:

```console
uv run python -m benchmarks.run --files 1000
uv run python -m benchmarks.run --files 10000
uv run python -m benchmarks.run --files 20000
```

Every run builds the same project twice and refuses nondeterministic semantic digests. The JSON
contains scan time, query time, peak child-process memory, brain size, and graph counts. Wall-clock
time is observational: shared CI machines vary, so pull requests should gate limits and determinism,
not a fragile time threshold. Keep published benchmark results with the commit, platform, Python
version, and command that produced them.

`--files` is the total regular-file count, including the synthetic README and `.rehearseignore`.
The 20,000-file profile therefore exercises the scanner's declared file-count boundary exactly.
