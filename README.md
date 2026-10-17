# Trogly

Talk to your RAID: Shadow Legends account in [Claude Code](https://claude.com/claude-code).

"What gear is on Thor Faehammer?" "Which of my Epics are fastest?" "How fast would Thor be with my spare Speed boots?" Trogly reads
your account from the running game (read-only; it never changes anything in game) and answers in conversation.

Trogly is a fan project. It is not affiliated with or endorsed by Plarium. RAID: Shadow Legends is a trademark of
Plarium Global Ltd.

## What you need

- Windows, with RAID installed and logged in on the same PC
- [Claude Code](https://claude.com/claude-code) on a Claude Pro or Max plan (no API key needed)
- Python 3.11 or newer
- The Trogly Agent (`Trogly.Agent.exe`), installed yourself. Keep it as new as the plugins: an older Agent answers
  newer requests with an error saying so

## Set up

1. Make an instance folder and copy `instance-template/` into it, for example `D:\Trogly`.
2. Install the MCP dependency: `python -m pip install "mcp>=1.28,<2.0.0"`.
3. In that folder, start Claude Code and run:

   ```text
   claude plugin marketplace add cavemancompanyllc/trogly
   claude plugin install trogly-core@trogly --scope project
   claude plugin install trogly-artifacts@trogly --scope project
   ```

4. Open RAID, then ask Trogly to refresh your account. The first time, Trogly asks where you installed
   `Trogly.Agent.exe` and remembers it.

## Plugins

| Plugin | What it adds |
| --- | --- |
| `trogly-core` | Account access (refresh, search champions and artifacts, one champion's gear and calculated stats, "what if they wore this" stats) and memory (goals, saved teams, saved gear loadouts, notes, and "what changed since..." history) |

| `trogly-artifacts` | Gear advice: "Which artifacts should I put on Thor? At least 200 speed, then max attack." Finds the best loadout from your own artifacts (vault and the champion's own gear by default; borrowing from other champions only if you allow it), with exact stats. Installs `trogly-core` too |

More are planned: champion and dungeon knowledge, dungeon teams, and arena teams.

## Privacy

Your account data, history, and plans stay in your instance folder (`data/`). Trogly sends nothing anywhere except your normal Claude
conversation.

## Known limits

- Champion stats are calculated by Trogly and not yet verified against the in-game screen.
- If you're logged in on your phone, the PC client gets signed out and its data stops updating.
- If Trogly's tools don't show up after you fix a setup problem, Claude Code may have cached the failure: remove the
  `plugin:trogly-core:trogly` entry from `~/.claude/mcp-needs-auth-cache.json` and restart Claude Code.
