---
name: trogly-account
description: Answer questions about the player's RAID Shadow Legends account (champions, gear, stats, resources, shards, Great Hall) from live game data via the trogly MCP tools. Use for any question about "my champions", "my gear", "what's on <champion>", "how many <shards/gems>", or when the player asks Trogly to refresh.
---

# Trogly account questions

The `trogly` MCP server reads the player's account through the Trogly Agent. Answer from its tools, never from
memory of RAID in general, when the question is about this player's account.

## Tools

| Question shape | Tool |
| --- | --- |
| "Refresh", "I just changed my gear", no data yet, data over ~30 minutes old | `refresh_account` |
| Refresh says the Agent location is unknown or wrong | Ask where `Trogly.Agent.exe` is installed, then `set_agent_path` |
| Account overview, resources, shards, Great Hall | `account_summary` |
| "Which of my champions...", "my fastest...", "do I have..." | `find_champions` |
| "What's on Thor?", "show me <champion>" | `get_champion` |
| "Do I have spare speed boots?", "where is my best Savage weapon?" | `find_artifacts` |

## Rules

1. **Check the snapshot.** Every result has `snapshot.read_at` and `age_minutes`. If there is a `warning`, tell the
   player how old the data is and offer `refresh_account` before relying on it.
2. **Stats are calculated.** Totals come from Trogly's stat calculator and are not yet checked against the in-game
   screen. Quote them, and say once per answer that they are calculated.
3. **Don't do stat maths in your head.** Don't add up substats to predict a new total. Trogly's what-if calculator is
   not built yet, so say what would change qualitatively and let the player check in game.
4. **Set bonuses come from the tool, not from memory.** `get_champion` returns `sets`: the in-game set name, pieces
   worn, whether it's active, the exact stats it grants now (`active_bonus_stats`), and when the next bonus unlocks.
   Variable sets give a bonus from the first piece. Never call a set incomplete or inactive unless `active` is false.
5. **Several copies.** If `get_champion` returns `ambiguous`, ask which copy (show stars, level, and gear count)
   instead of guessing.
6. **Read-only.** Trogly never changes the game. Recommendations are things the player does in game themselves.
7. **Tools missing means stop.** If the `trogly` tools aren't available, say Trogly's tool server isn't connected
   and suggest restarting Claude Code in the Trogly folder. Never read files under `data/` yourself; the raw state
   file lacks the set and stat interpretation the tools add, so answers built from it are wrong.
8. **Refresh failures.** If refresh fails, relay the error's first line. The common cause is RAID logged in on a
   phone, which signs the PC client out. Never suggest the player share their login details.

## Answer style

Lead with the answer. For a champion, give the stat line, then the gear by slot (set, main stat, notable
substats), then anything that stands out, such as missing pieces, low-rank or unlevelled gear, inactive sets that are one piece short, or crit rate over 100%.
