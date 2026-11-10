# Plugin contracts

Use the supported analyzer, artifact-importer, restore-provider, or inspection-check protocol and
declare a stable lowercase ID, API version, plugin version, description, and capabilities. Return
only validated bounded models with deterministic keys and provenance.

Reject incompatible core API versions and duplicate IDs. Do not depend on import order, working
directory, network state, user environment values, or wall-clock ordering for semantic output.
