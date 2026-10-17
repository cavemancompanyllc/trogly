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
4. **Don't judge set bonuses from memory.** Many newer sets give bonuses from one or two pieces, and the tools don't
   report active set bonuses yet. List the set pieces, but don't claim a set is incomplete or inactive.
5. **Several copies.** If `get_champion` returns `ambiguous`, ask which copy (show stars, level, and gear count)
   instead of guessing.
6. **Read-only.** Trogly never changes the game. Recommendations are things the player does in game themselves.
7. **Refresh failures.** If refresh fails, relay the error's first line. The common cause is RAID logged in on a
   phone, which signs the PC client out. Never suggest the player share their login details.

## Answer style

Lead with the answer. For a champion, give the stat line, then the gear by slot (set, main stat, notable
substats), then anything that stands out, such as missing pieces, low-rank gear, or an unfinished set.
