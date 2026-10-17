---
name: arena-team
description: Build RAID Shadow Legends arena teams from the player's own champions, or check an arena team's speed tune ("set up my Tag Arena", "what should my Classic Arena defence be", "is my arena team speed tuned", "who goes first in this team", "what SPD does my booster need"). Use for Classic Arena, Tag Arena, and turn order or speed tune questions about any team.
---

# Arena team

1. **Building teams: delegate to the `arena-scout` subagent.** Pass it:
   - the mode: Classic Arena (defence or offence) or Tag Arena
   - any champions the player wants included or left out
   - opponents the player mentioned, with their speeds if given
   - whether to save the teams

   It reads the roster and kits through the tools, checks each team's turn order with `speed_tune`, and returns the
   teams with reasons. Relay its answer.
2. **Checking a team the player already has:** call `speed_tune` yourself with the team in slot order, leader first,
   and the area (`Arena` for Classic, `Arena3X3` for Tag, `""` for content where no aura applies, `Dungeon` for
   dungeons). Show the first turns, the leader's aura and whether it works there, and anything in `not_simulated`.
   For "what SPD does X need", try `speed` overrides on that champion until the order works, and give the lowest
   SPD that does.
3. **Gear the team.** If the answer says a champion needs more SPD or ACC, offer to run the `gear-smith` subagent
   for that champion with that target.
4. **Follow-ups.** For "what about X instead?", ask `arena-scout` again with that change, or re-run `speed_tune` for
   a quick turn order check. Don't swap champions without checking their kit with `champion_kit`.

Never state a turn order, aura effect, or skill from memory. Use the tools.
