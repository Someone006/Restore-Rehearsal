# Homebrew tap preparation

The release workflow produces an immutable source archive, `SHA256SUMS`, and a matching
`restore-rehearsal.rb` release asset. To reproduce that formula locally after the tagged archive
exists:

```console
python -m scripts.generate_homebrew_formula \
  --sdist release/restore_rehearsal-0.3.0.tar.gz \
  --url https://github.com/Someone006/Restore-Rehearsal/releases/download/v0.3.0/restore_rehearsal-0.3.0.tar.gz \
  --version 0.3.0 \
  --output Formula/restore-rehearsal.rb
```

The generator takes the direct Python dependency URLs and hashes from `uv.lock`, refuses an
existing output, and requires a credential-free HTTPS source URL. The generated formula belongs in
a separately administered Homebrew tap. Creating that tap or pushing to it requires an external
account and is intentionally not automated from this repository.
