---
name: find-upgrades
description: Find RAID Shadow Legends artifacts worth upgrading, with the odds that their remaining rolls land on useful stats, optionally for one set, slot, or kind of build. Use when the player asks what to upgrade, how to spend silver, which pieces to push during an artifact enhancement event, or how to build a better set (for example "a good Immortal set").
---

# Find upgrades

Delegate to the `quartermaster` subagent with:

- the goal in the player's words ("good Immortal set for my tank", "speed boots", "anything worth it")
- any set, slot, or star limits
- whether pieces already worn count (by default they do)

Relay its answer.

## Odds

The odds come from `upgrade_candidates`: exact chances over the remaining upgrades (levels 4, 8, 12, and 16),
assuming each roll is random. A level-0 Legendary can end with up to 8 useful hits, an Epic 7, a Rare 6. Never
quote odds the tool didn't give.

When the player settles on pieces to upgrade later (for example during the next enhancement tournament), offer to
note the plan with trogly-core's `notes` tool (kind decision) so it's there next time.
