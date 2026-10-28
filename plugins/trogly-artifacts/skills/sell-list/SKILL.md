---
name: sell-list
description: Find RAID Shadow Legends artifacts to sell from the player's vault, with a reason for each, while protecting saved builds and keeping the best spares per slot and set. Use when the player asks what gear to sell, wants to clear vault space, or asks which pieces are junk.
---

# Sell list

Delegate to the `quartermaster` subagent and pass on:

- that the player wants a sell list
- anything they want kept ("keep all my Lifesteal", "don't touch Mythical pieces")
- how strict they want it to be ("only the obvious junk" means higher keep settings; "clear lots of space" means
  lower)

Relay its answer. For a quick follow-up ("why sell that boot?"), the sell row's `why` field has the reason.

Pieces in saved builds (trogly-core `loadouts`) are always protected. Trogly doesn't sell anything; the player does
it in game.
