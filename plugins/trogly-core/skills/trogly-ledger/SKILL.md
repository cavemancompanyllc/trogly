---
name: trogly-ledger
description: Keep the player's RAID plans across conversations with Trogly's ledger - goals, saved teams, saved gear loadouts, decision and session notes, and account history ("what changed since yesterday"). Use when the player sets a goal, settles on a team or gear plan, asks what changed on their account, or starts or wraps up a planning session.
---

# Trogly ledger

The ledger lives in the instance at `data/trogly.db`. Reach it only through the `trogly` MCP tools. Never open the
file yourself.

## When to use which tool

| Moment | Do this |
| --- | --- |
| Start of a planning conversation | `notes` (list, kind session) and `goals` (list), then mention anything relevant in one or two lines |
| Player says what they're working towards | Offer `goals` add, with the champion and target if there is one |
| A team is agreed | Offer `teams` save, with roles and a purpose like "Phantom Shogun 20" |
| A gear plan is agreed or the player is happy with current gear | Offer `loadouts` save. It defaults to the gear worn now |
| "What does Thor wear for Clan Boss?" | `loadouts` list with `encounter` (`clan_boss` finds every difficulty), then compare |
| "Did I take Thor's gear for someone?" | `loadouts` compare: each saved piece shows equipped, in the vault, or worn by someone |
| "What changed since...?" | `account_diff`. Refresh first if they want it up to now |
| A choice with a reason worth keeping | `notes` add, kind decision |
| End of a planning conversation | `notes` add, kind session, with body (what was done) and next_steps |

## Rules

- **Offer, don't assume.** Ask before saving goals, teams, or loadouts; a one-line "Save this as 'Shogun 20'?" is
  enough. Session notes at the end of a planning conversation don't need asking.
- **Diffs use calculated stats.** Say so when you quote stat changes.
- **History only knows refreshes.** Changes between two refreshes are merged into one diff, so "since yesterday"
  means since the last refresh on or before yesterday.
- **Missing members.** A saved team member marked `missing` was fed, sold, or otherwise left the account. Point
  it out and offer to update the team.
