# Hyperframes Composition Brief: Restore Rehearsal

## Objective

Create a short launch-style brag video for Restore Rehearsal that makes two grounded ideas
immediately clear: a backup is not proof until it restores, and a project is easier for people and
AI agents to reason about when its evidence remains connected locally.

## Output

- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080
- Duration: 22.93 seconds

## Source Material

- Project root: current Restore Rehearsal repository
- Primary files read: `site/index.html`, `site/assets/site.css`, `README.md`, `docs/brain.md`,
  `docs/recipe.md`
- Product name: Restore Rehearsal
- Tagline / strongest claim: “Test whether backups restore before production.”
- Key UI or visual moment to recreate: terminal-driven Project Brain build, connected evidence
  graph, and the disposable restore/verify/upgrade sequence
- Copy that must appear verbatim:
  - “Backups are promises.”
  - “Restores are proof.”
  - `rehearse brain build . --output project.rrbrain`
  - “Know your project. Prove it can recover.”
  - “Open source · local first · Apache-2.0”

## Creative Direction

- Tone preset: polished
- Creative direction: quiet operational confidence; evidence replaces hand-waving
- Interpretation: measured cuts, generous reading holds, crisp terminal/graph motion, and sparse
  audio. No comedy, inflated metrics, or generic SaaS claims.
- Angle: turn a local project into connected evidence, turn a backup into a checked disposable
  restore, then resolve both into one private `.rrbrain` file.
- Hook: “Backups are promises. Restores are proof.”
- Outro / punchline: “Know your project. Prove it can recover.”
- Avoid:
  - Generic SaaS language
  - Abstract filler visuals
  - Unrelated visual redesign
  - Claims of guaranteed recovery, compliance, certification, or production safety
  - Remote assets, remote fonts, analytics, telemetry, login, cloud rendering, or publishing

## Visual Identity

- Background: `#07110f`
- Text: `#f3f8f6`
- Accent: `#73e6b5`
- Display font: bundled Inter 900
- Body font: bundled Inter 400/700
- Visual references from the project: mint-on-deep-green landing page, bordered evidence cards,
  monospace commands, graph connections, and the single-file `.rrbrain` model

LOOK: deep-green solid ground `#07110f`; off-white ink `#f3f8f6`; mint evidence accent `#73e6b5`;
raised panels `#122521`; borders `#29453f` at 2px video weight; Inter 900 display and Inter 400/700
body; 72px safe outer margin; restrained radial mint light no stronger than 22% opacity; no remote
fonts or texture assets.

BANNED: generic blue-purple gradients, glassmorphism stacks, floating blobs, decorative particle
fields, breathing titles, fake dashboards, invented statistics, click rings, and perpetual loops.

## Storyboard

Use `brag-output/brag-plan.md` as the creative contract.

Scene summary:

1. Promise versus proof — 3.27s — two-line hook on the real project palette.
2. Build the brain — 5.47s — terminal command builds a connected project evidence graph.
3. Rehearse recovery — 4.37s — backup enters a disposable stack; restore, verify, upgrade resolve.
4. One private file — 4.36s — architecture, change impact, and recovery evidence connect to one
   `.rrbrain` file.
5. Product lockup — 5.46s — “Know your project. Prove it can recover.” plus URL, license, and music
   credit.

## Audio

- Audio role: restrained steady support with sparse professional accents
- Audio arc: quiet tension under the promise/proof hook, controlled motion through the graph and
  restore checks, one warm consolidation hit, then a clean fade under the final lockup
- Music: `happy-beats-business-moves-vol-12-by-ende-dot-app.mp3`
- Music treatment: start at 0s around 0.25 volume; stay under copy; fade over final 0.9s
- Music cue guidance: bundled preset
  `happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json`; target major seams at 8.74s,
  13.11s, and 17.47s; use every other beat for readable sequences
- Audio-reactive treatment: subtle; local RMS/bass may add at most 4% visual presence to the graph
  halo and `.rrbrain` file border. No waveform or equalizer visuals.
- Audio-coupled moments:
  - Terminal confirmation — quiet click
  - Paired graph-node arrivals — every-other-beat motion; only one or two soft accents
  - Restore/verify/upgrade — low-risk click accents at each visible check
  - `.rrbrain` consolidation — warm soft impact
  - Final lockup — restrained bell at 17.47s
- SFX selection guidance: prefer low-risk `interface/click_003.ogg` and
  `impact/impactSoft_medium_001.ogg`; use `impact/impactBell_heavy_000.ogg` only once on the outro
- SFX analysis guidance: installed `/brag` `assets/sfx/sfx-analysis.md`
- Exact SFX choice: Hyperframes should choose filenames, timestamps, density, and volume based on
  the implemented animation.
- Audio files: copy only selected music and SFX into `brag-output/composition/assets/`

## Hyperframes Instructions

Use the pinned local Hyperframes 0.9.31 toolchain with telemetry disabled. Follow its current
composition contract, data attributes, bundled-font rules, seek-safe GSAP motion, local-media rules,
registry search, lint/check/snapshot/render workflow, and accessibility audits. `/brag` owns the
story; do not create a separate Hyperframes storyline or `SPEC.md`.

Requirements:

- Show the actual documented command and product-shaped Project Brain/recovery flow.
- Keep every line readable at phone size and inside title-safe bounds.
- Keep total duration exactly 22.93 seconds at 30fps-compatible timing.
- Use only local assets and deterministic finite timelines.
- Mark the 8.74s, 13.11s, and 17.47s seams as beat-locked in composition comments.
- Mark readable sequential timing choices as beat-grid or natural-timing decisions in comments.
- Include local music/SFX and visible CC BY 4.0 music attribution.
- Run the full `hyperframes check --json --snapshots` gate and ensure real text is contrast-audited.
- Keep creation and rendering local. Never run auth, capture, login, cloud, publish, feedback, TTS,
  or any provider command.
