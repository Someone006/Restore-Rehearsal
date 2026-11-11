# Restore Rehearsal launch film

This directory contains the local `/brag` launch-film deliverables:

- `brag.mp4` — 1920×1080, 30 fps, 22.93-second delivery render with stereo audio
- `brag.jpg` — poster image and the video's poster-safe first frame
- `share-copy.txt` — ready-to-review social copy with required music attribution
- `brag-plan.md` and `composition-brief.md` — narrative and production decisions
- `composition/` — inspectable Hyperframes source with local media and animation runtime
- `THIRD_PARTY.md` — media, runtime, and creation-tool notices

Nothing here was uploaded to a cloud renderer. The composition uses only local assets and its npm
scripts disable Hyperframes telemetry. There is intentionally no publish script.

## Reproduce locally

Node.js 22 or newer, Chromium, FFmpeg, and FFprobe are required.

```console
cd brag-output/composition
npm ci --ignore-scripts
npm run check -- --snapshots
npm run render -- --quality delivery --output ../brag.mp4
```

The lockfile pins Hyperframes 0.9.31 and overrides Undici to 7.30.1. Review the generated output
and [third-party notices](THIRD_PARTY.md) before redistribution.
