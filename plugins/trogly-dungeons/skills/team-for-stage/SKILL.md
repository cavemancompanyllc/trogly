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
3. **Write the report.** Fill `report-template.md` (next to this file) from the scout's answer and the tools, and
   write it to `reports/teams/<place>-<difficulty>-<stage>.md` in the Trogly folder (the folder Claude Code runs
   in), e.g. `reports/teams/doom-tower-hard-57-r2.md`. Lowercase, hyphens, no spaces.
   - Build check: take masteries, blessing, and gear from `get_champion`. Write "not read" for anything the tools
     don't show (relics have no owner yet); never fill it from memory.
   - If the file exists, the player has fought this before: keep their **Result** notes and add a dated section
     instead of overwriting.
   - Add or update one line in `reports/index.md`: `- [<title>](teams/<file>.md) — <date>, <team in one line>`.
   - Save the team with `teams` (action save) and put the report path in its notes, unless the player said not to.
   - Tell the player the file path. Offer to publish it as a shareable page only if they ask to share it.
4. **Follow-ups.** For "what about X instead?", ask `dungeon-scout` again with that change. Don't swap champions
   yourself without checking their kit with `champion_kit`. Update the same report.

Never suggest champions or explain boss mechanics from memory. Use the tools.
