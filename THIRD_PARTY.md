# Third-party runtime dependencies

Rehearse installs these direct runtime dependencies from their own distributions:

| Package | Purpose | Declared license |
| --- | --- | --- |
| [PyYAML](https://pyyaml.org/) | Strict YAML parsing for recipes and project artifacts | MIT |
| [pathspec](https://github.com/cpburnz/python-pathspecification) | Git-compatible ignore matching for ordinary local folders | MPL-2.0 |

Rehearse does not vendor or modify either package. Their installed distributions contain their
license texts. Transitive build and development dependencies are recorded in `uv.lock`; release
users should generate an SBOM for the exact environment they distribute. This inventory is
informational and is not legal advice.
