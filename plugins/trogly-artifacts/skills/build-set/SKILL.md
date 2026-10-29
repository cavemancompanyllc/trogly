---
name: build-set
description: Build or judge RAID Shadow Legends artifact sets from the player's own gear - "my best Stun set", "a Feral set with the best companion set", "is Stone Skin worth it on Duchess", "which sets for Vergis against Borgoth", or gearing a whole team. Use when the player talks about sets, set bonuses, stats versus sets, or set tiers.
---

# Build sets

Trogly's set tools do the judging; you pick the inputs and explain.

## Which tool

| Player asks | Do this |
| --- | --- |
| "Which sets suit Thor?" / "...for Clan Boss?" | `set_advice` with champion (and encounter key, as in `gear-for-encounter`) |
| "My best Stun set" | `rank_set_pieces` (set_name, profile or stats) for the best pieces per slot, then `optimize_gear` with `require_sets: {"Stun": 4}` for an exact loadout |
| "Feral with the best companion set" | `set_advice` for the champion to pick companion sets (2-piece sets with a high value), then `optimize_gear` with both, e.g. `require_sets: {"Feral": 4, "Speed": 2}`; compare two or three companions with `compare_set_options` (`sets` lists the options) |
| "Is Stone Skin worth it on X?" / "stats or sets?" | `compare_set_options` (champion, maximize, minimums; `sets` to name candidates) |
| Gear a whole team | `optimize_team_gear` with members in priority order (carry or speed lead first) |
| "Lifesteal is better than that" / "I'm late game now" | `set_preferences` (set_tier, gear_stage) |
| "Always use Destroy against Borgoth" | `set_preferences` add_rule (encounter kind such as doom_tower, boss, set, multiplier) |

## Explaining

- **Value** = tier x fit x encounter. Say which part matters: "Perception is S+, but ACC does nothing for
  Thor" or "Fyro is immune to Stun, so the Stun set is wasted there".
- **Stats or sets** depends on the player's gear stage (early: stats first; late: full sets first).
  `compare_set_options` gives a verdict for the maximized stat only. Point out any other big stat drops (crit
  damage falling by 30% matters even if ATK barely moves).
- **Tiers** are a community list plus the player's changes. Entries marked `estimate` are Trogly's guesses;
  invite the player to correct them with `set_preferences`.
- Never estimate stats or odds. Trogly doesn't move gear; the player does.
