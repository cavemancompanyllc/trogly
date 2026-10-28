---
name: gear-for-encounter
description: Gear RAID Shadow Legends champions for one encounter (Classic, Tag or Live Arena, a Clan Boss difficulty, Hydra, Chimera, Siege, a dungeon or Doom Tower stage, wave clearing) and save the build so it can be recalled later. Use when the player asks what a champion should wear for some content, wants to save or reuse a build for it, or asks "what does Thor wear for Clan Boss?".
---

# Gear for an encounter

A build is a saved loadout tagged with an encounter, so "what does Thor wear for Nightmare Clan Boss?" can be
answered next month. Builds live in trogly-core's `loadouts` tool.

## 1. Work out the encounter key

| Player says | Key |
| --- | --- |
| Classic / Tag / Live Arena | `arena:classic`, `arena:tag`, `arena:live` |
| "UNM", "Ultra Nightmare Clan Boss" | `clan_boss:ultra_nightmare` (also `easy`, `normal`, `hard`, `brutal`, `nightmare`) |
| Hydra, Chimera | `hydra:<difficulty>`, `chimera:<difficulty>` |
| Siege | `siege:attack` or `siege:defense` |
| A dungeon, campaign, or Doom Tower stage ("Fire Knight hard 1", "Dragon 25") | `stage:<stage id>`: call trogly-knowledge's `find_stage` and use its `encounter` field |
| Not one place ("wave clearing", "farming") | `general:<label>`, for example `general:wave_clear` |

If trogly-knowledge isn't installed, a stage can't get its id. Say so, and save under `general:<label>` naming the
stage (`general:fire_knight_hard_1`).

## 2. Look for a saved build first

`loadouts` action list with `encounter` (and `champion` or `team` if given). A kind without a difficulty
(`clan_boss`) lists every difficulty.

- **A build exists:** run `loadouts` compare on it and show where each piece is now (equipped, vault, or worn by
  someone else). Offer to rebuild it if pieces are gone or the player says it's out of date.
- **No build:** go to step 3.

## 3. Build it

For one champion, delegate to the `gear-smith` subagent with the champion, the encounter key, the goal in the
player's words, and champions whose gear must not be touched. gear-smith turns the encounter into stat targets.

For a team, gear one champion at a time in priority order (the carry or the speed lead first), and protect each
finished champion with `never_take_from` for the next one.

## 4. Save it

Offer one line: "Save as Thor's 'UNM' build?" Then `loadouts` save with:

- `name`: short, the player's words ("UNM", "Arena offence")
- `encounter`: the key from step 1
- `team`: the saved team's name when the build belongs to one (`teams` list shows them)
- `artifact_ids`: the recommended pieces (leave it empty to save what the champion wears now)
- `target_stats`: the targets used ("SPD 171-189, ACC 250+")

When a build is replaced, offer `loadouts` update with `status: retired` on the old one instead of deleting it, so
battle records made with it still make sense.

## Rules

- Never invent encounter keys. The `loadouts` tool rejects unknown ones and lists the forms it accepts.
- Never estimate stats. Quote `optimize_gear` or `calc_stats`.
- Trogly doesn't move gear. The player equips the pieces in game.
