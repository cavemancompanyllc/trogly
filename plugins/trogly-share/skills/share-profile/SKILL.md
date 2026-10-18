---
name: share-profile
description: Share the player's RAID Shadow Legends account with friends on trog.ly ("share my account", "publish my profile", "send my roster to a friend", "invite a friend to trog.ly", "delete my trog.ly profile", "what does trog.ly have about me"). Publishing is always previewed and confirmed first.
---

# Share a profile on trog.ly

trog.ly shows a snapshot of an account behind an unguessable link, for friends. It's a fan project, friends-only for
now, with no game art.

## Publishing

1. Call `share_status`. If there's no publish key, the player needs an invite code from a friend: ask for it and a
   handle (3-24 characters: lowercase letters, digits, `_`, `-`), then `redeem_invite`.
2. If the account data is old (over about 30 minutes, see trogly-core), offer `refresh_account` first.
3. Call `preview_publish` and show the player, briefly:
   - sections, champion count, equipped artifact count, size
   - **everything in `never_sent`**, as a short list
4. Ask "Publish this?" and wait. Only on a clear yes, call `publish_profile` with `confirm=true`, and give the link.
   Gear is in by default; `great_hall` is opt-in (`sections=["champions","gear","great_hall"]`).

## Other requests

- **Invite a friend:** `create_invite` gives a one-use code valid for 14 days. The player sends it themselves.
- **Delete:** `unpublish_profile` removes the profile, every snapshot, and the publish key from the site. It's
  permanent, so ask first, then call it with `confirm=true`.
- **Update:** publishing again replaces the snapshot; the site keeps the last three.

## Rules

- Never publish without a preview and a yes in this conversation.
- Never show the publish key; it lives in `data/share.json` on this PC.
- The site only shows what was published. Don't describe how Trogly reads the game.
