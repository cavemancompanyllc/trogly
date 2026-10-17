---
name: team-for-stage
description: Build a RAID Shadow Legends team from the player's own champions for a dungeon or stage ("help me beat Phantom Shogun 20", "what team for Dragon 25", "who should I take to Ice Golem 20"). Use whenever the player wants a team, lineup, or comp for specific content.
---

# Team for a stage

1. **Delegate to the `dungeon-scout` subagent.** Pass it:
   - the place, stage number, and difficulty if given
   - any champions the player wants included or left out
   - whether to save the team

   It reads the boss's kit and the player's roster through the tools and returns a team with reasons. Relay its answer.
2. **Gear the team.** If the answer says a pick needs gear (more ACC or SPD, say), offer to run the `gear-smith`
   subagent for that champion with those targets.
3. **Follow-ups.** For "what about X instead?", ask `dungeon-scout` again with that change. Don't swap champions
   yourself without checking their kit with `champion_kit`.

Never suggest champions or explain boss mechanics from memory. Use the tools.
