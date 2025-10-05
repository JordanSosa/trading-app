# Endless Embers – Systems Overview

This document captures the high-level design intent for Endless Embers and serves as a guide while implementing the detailed systems.

## Simulation Loop
- Runs at 10Hz (100ms delta) and must remain deterministic under a seeded RNG.
- Tick order: apply statuses → hero AI → enemy AI → cooldown updates → damage resolution → wave/boss checks → reward handling.

## Combat Formulas
- **Damage Pipeline:** Base → additive % modifiers → multiplicative % modifiers → crit → penetration/resistance → on-hit procs → DoT application.
- **Status Effects:**
  - *Ignite:* 40% of hit as Fire DoT over 4s, stacking refreshes duration and adds value.
  - *Chill/Freeze:* 10–30% slow; large hits over threshold freeze targets.
  - *Shock:* 10–30% increased damage taken.
- **Resistances:** Physical, Fire, Cold, Lightning, Poison with a 75% cap unless extended by uniques.

## Content Goals
- 5 zones (T1→T5) each with themed enemy families, modifiers, and catalysts.
- 3 archetypes (Pyromancer, Frost Warden, Storm Blade) with 4 actives + 1 capstone each.
- 8 gear slots with affix-driven stat budgets across rarities.
- 60+ affixes, 24 base items, and 12 legendary items with build-defining powers.

## Crafting Loop
- Salvage → shards.
- Reroll → replace a specific affix with a weighted slot-valid alternative.
- Augment → add a missing affix (if slot available).
- Upgrade → attempt to raise tier with pity counter safety and catalyst boosts.

## Persistence
- LocalStorage saves with `{ schemaVersion, timestamp, seed, hero, inventory, settings }`.
- Migrations run on load to upgrade prior versions.
- Offline catch-up processes up to 8 hours of progress, gated by a Rested Charge resource.

## Testing Strategy
- Determinism checks via seeded simulations (60s DPS consistency).
- Affix legality validation for generated loot.
- Crafting safety to prevent affix loss or tier overflow.
- Offline reward caps and tooltip accuracy tests.

These notes will expand as the implementation matures.
