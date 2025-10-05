# Endless Embers

An idle-meets-ARPG prototype where a single hero endlessly clears zones to earn loot and progress. This repository currently ships the project scaffold, including build tooling, folder structure, and initial content placeholders for the Endless Embers MVP.

## Getting Started

```bash
npm install
npm run dev
```

- The development server runs via Vite on port 5173 by default.
- Tailwind CSS utilities are available globally through `src/index.css`.
- Routing is handled with `react-router-dom` and is preconfigured with the major MVP screens.

## Project Layout

```
/content           JSON-driven game content (affixes, items, zones, talents, skills)
/docs              High-level documentation and design notes
/src
  /app             Cross-cutting application utilities and providers
  /game            Core simulation, data loaders, and gameplay systems (placeholders for now)
  /state           Zustand stores and persistence helpers
  /ui              UI components and screen-level views
  /utils           Shared helpers (logging, formatting, etc.)
/tests             Vitest suites for combat, loot, crafting, and systems validation
```

## Next Steps

This scaffold is the foundation for building the full Endless Embers experience outlined in the design brief. Upcoming milestones include:

1. Implementing the deterministic 10Hz simulation loop and combat math in `src/game/core`.
2. Authoring the hero, enemy, and zone behaviors in `src/game/sim` using seeded RNG utilities.
3. Expanding the JSON content packs (60+ affixes, 24 base items, 12 legendaries, etc.).
4. Wiring up gameplay UI screens for inventory management, crafting, and zone selection.
5. Adding save/load flows with schema migrations and offline progress handling.
6. Building comprehensive Vitest coverage for determinism, crafting safety, tooltip deltas, and more.

Refer to [`docs/DESIGN.md`](docs/DESIGN.md) for an overview of the systems vision and guiding formulas.
