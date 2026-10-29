---
name: clan-boss-sim
description: Simulate RAID Shadow Legends Clan Boss (Demon Lord) battles for the player's own champions and publish the results as a report. Use for "make 5 Clan Boss teams and simulate them", "how much damage would this team do on Nightmare", "compare these two Demon Lord teams", or "which of my champions should go in my clan boss team".
---

# Clan Boss simulator

`clan_boss_teams` plays whole Clan Boss battles in code from the game's own rules (turn order, damage, DEF, crits,
ACC against RES, buffs and debuffs, cooldowns, the boss's skills and damage caps). No AI picks each turn: champions
use their skills in order, skipping ones that would do nothing (no revive with nobody dead). You only read the
summary it returns.

## Steps

1. **Pick the call.**
   - "Make N teams": no `teams`, `count` = N. Pass `include` / `exclude` for champions the player names.
   - "Simulate this team" or "compare these": `teams`, each a list of champion names, leader first, or
     `{"label": "...", "champions": [...]}`.
   - A champion can carry a skill order: `{"champion": "Maneater", "priority": ["A3", "A1"], "never": ["A2"],
     "delay": {"A3": 1}}` (delay: skip that skill until after the champion's Nth turn). Use it for unkillable
     tunes and openers the player describes.
   - `runs`: battles per team (default 10; up to 500). Every team faces the same seeds.
   - `difficulty` (Easy to UltraNightmare) and `affinity` (Void, Magic, Force, Spirit) as the player says.
2. **Pick the analysis mode the player asked for.** Default `claude`.
   - `claude`: explain the ranking from the returned `digest` yourself.
   - `ollama`: the local model writes the analysis; relay `analysis.text` with the ranking and add nothing of your
     own. Use it when the player wants to save tokens.
   - `opus`: pass the returned `digest` to the `battle-analyst` agent and relay its write-up. Use it when the player
     wants the most careful read.
3. **Publish the report.** Publish `report_file` with the Artifact tool exactly as written (it's a finished page; don't
   rewrite or restyle it). Give the link with a short ranking.

## Rules

- **Say what the numbers are.** They're simulated, from the game's code and the account's calculated stats. Mention
  the `assumed` list when it matters (for example, masteries aren't simulated, so Warmaster or Giant Slayer teams do
  more than shown), and any champion whose coverage is under 85%.
- **Don't add game rules from memory.** If a result looks wrong, say so and point at `not_simulated`, rather than
  correcting it with remembered mechanics.
- **Boss stats need a recent knowledge file.** If the tool says the knowledge file has no boss stats, run
  `refresh_knowledge` with RAID open.
