# Package verification

Build source and binary artifacts from a clean, exact revision. Verify metadata, license files,
version consistency, archive contents, executable entry points, dependency bounds, installation in
a fresh environment, and primary workflows from the installed artifact—not the checkout.

Where supported, build twice with a fixed source date and compare normalized hashes. Explain any
known nondeterminism rather than claiming reproducibility from a single build.
