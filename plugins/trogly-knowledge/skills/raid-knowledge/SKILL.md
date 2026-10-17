---
name: raid-knowledge
description: Answer RAID Shadow Legends questions about what champions' skills do and what a dungeon or stage contains, from the game's own data via the trogly-knowledge tools. Use for "what does X's A3 do", "who of my champions has Decrease DEF", "what's in Phantom Shogun 20 / Dragon 25", boss skills or boss stats, and before suggesting champions for content.
---

# RAID knowledge

The `trogly-knowledge` tools read RAID's own static data: the same skill text, cooldowns, and stage setups the game
uses. Prefer them over what you remember about RAID, which may be outdated or wrong for this game version.

| Question | Tool |
| --- | --- |
| "What does Thor's A3 do?", "is Fayne's debuff 60%?" | `champion_kit` |
| "Which of my champions place Decrease DEF?", "who can revive?" | `search_kits` (owned only by default) |
| "What's in Phantom Shogun 20?", "Dragon 25 boss skills" | `find_stage` |
| After a game update, or when kits for a new champion are missing | `refresh_knowledge` |

## Rules

- **Quote the game.** When explaining a skill, use its in-game description and the listed buffs and debuffs (with
  chance and turns), not a summary from memory.
- **Bosses have their own kits.** A boss's passives (immunities, damage caps, counters) come from `find_stage`'s
  `boss_kits`. Read them before suggesting what works against the boss.
- **Stage modifiers are the boss's extra stats.** "boss: SPD +175" is added to the boss's own speed. Use them to judge
  how fast or accurate the team needs to be, and say they are the stage's added stats, not totals.
- **Affinity comes from the tool.** Use `affinity_matchup` (on a boss kit or a champion kit) for which affinities land
  strong or weak hits. Never work it out from memory. Weak hits are often worse than normal ones (some bosses punish
  them), so recommend attackers from `strong_hits_from` or `normal_hits_from`.
- **Knowledge isn't the player's roster.** For what the player owns and how it's built, use trogly-core.
  `search_kits` lists owned copies with stars and level to connect the two.
