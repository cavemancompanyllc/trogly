---
name: gear-champion
description: Recommend gear for one RAID Shadow Legends champion from the player's own artifacts using Trogly's gear optimiser. Use when the player asks which artifacts to put on a champion, how to make a champion faster, tankier, or hit harder, or how to build a champion for some content.
---

# Gear a champion

Delegate to the `gear-smith` subagent. Pass it:

- the champion
- the goal in the player's words
- any numbers, sets, or limits the player gave
- champions whose gear must not be touched

The subagent runs `optimize_gear` and explains the result. Relay its answer.

If you handle a quick follow-up yourself ("what if I keep my old boots?"), use `calc_stats`. Never estimate stats.

## Writing the request

| Player says | `optimize_gear` arguments |
| --- | --- |
| "Make Thor faster but keep his damage" | `maximize: "SPD"`, `minimum: {"ATK": <current ATK minus a little>}`; ask how much damage they'd give up if it matters |
| "SPD 200+, then max ATK" | `maximize: "ATK"`, `minimum: {"SPD": 200}` |
| "Full Speed set, 100% crit" | `require_sets: {"Speed": 4}`, `minimum: {"C.RATE": 100}` |
| "Use anything except Arbiter's gear" | `use_other_champions_gear: true`, `never_take_from: ["Arbiter"]` |

Crit stats are percentages (100 = 100%). Set names are the in-game names `get_champion` shows (Speed, Perception,
Savage, Lifesteal, Stone Skin, and so on).
