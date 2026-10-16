# Trogly

Talk to your RAID: Shadow Legends account in [Claude Code](https://claude.com/claude-code).

"What gear is on Thor Faehammer?" "Which of my Epics are fastest?" "Do I have spare Speed boots?" Trogly reads
your account from the running game (read-only; it never changes anything in game) and answers in conversation.

Trogly is a fan project. It is not affiliated with or endorsed by Plarium. RAID: Shadow Legends is a trademark of
Plarium Global Ltd.

## What you need

- Windows, with RAID installed and logged in on the same PC
- [Claude Code](https://claude.com/claude-code) on a Claude Pro or Max plan (no API key needed)
- Python 3.11 or newer
- The Trogly Agent (`Trogly.Agent.exe`), installed yourself

## Set up

1. Make an instance folder and copy `instance-template/` into it, for example `D:\Trogly`.
2. Install the MCP dependency: `python -m pip install "mcp>=1.28,<2.0.0"`.
3. In that folder, start Claude Code and run:

   ```text
   claude plugin marketplace add <this repo>
   claude plugin install trogly-core@trogly --scope project
   ```

4. When asked, set **Trogly Agent** to the full path of `Trogly.Agent.exe`.
5. Open RAID, then ask Trogly to refresh your account.

## Plugins

| Plugin | What it adds |
| --- | --- |
| `trogly-core` | Account access: refresh, search champions and artifacts, one champion's gear and calculated stats |

More are planned: gear optimisation, champion and dungeon knowledge, dungeon teams, and arena teams.

## Privacy

Your account data stays in your instance folder. Trogly sends nothing anywhere except your normal Claude
conversation.

## Known limits

- Champion stats are calculated by Trogly and not yet verified against the in-game screen.
- If you're logged in on your phone, the PC client gets signed out and its data stops updating.
- A few artifact set names are shown as split-up internal codes.
