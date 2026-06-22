# DISCORD BOT SETUP (for two-way approvals / `comms.read`)

A Discord **webhook cannot read messages** — it is write/POST-only. To READ your approval replies you must use a real Bot user with a token and the Message Content Intent. Outbound (`comms.post`) keeps working via `COMMS_WEBHOOK_URL` even before you do this.

## Create the bot
1. Open the Discord Developer Portal (https://discord.com/developers/applications) and sign in.
2. Click **New Application**, name it, accept terms, **Create**. Note the **Application ID** (Client ID) on General Information.
3. Open the **Bot** tab in the left sidebar (a bot user is attached automatically; click **Add Bot** only if an older portal shows it).
4. Under **Privileged Gateway Intents**, toggle **ON** "Message Content Intent" (required — without it, REST reads return empty `content` for messages the bot didn't send/isn't mentioned in). Leave Presence and Server Members OFF.
5. Recommended: turn **OFF** "Public Bot" so only you can add it.

## Get the token
6. On the **Bot** tab click **Reset Token**, confirm, and **copy it immediately** (shown once). Store as `DISCORD_BOT_TOKEN` in `.env` — never commit. If leaked, Reset again.
7. The REST header is `Authorization: Bot <TOKEN>` (literal word "Bot" + space + token). This is NOT the OAuth2 Client Secret.

## Invite the bot to your server
8. Build the invite URL with scope `bot` and permissions = View Channel + Send Messages + Read Message History (**68608**):
   `https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot&permissions=68608`
9. Open it, pick your server, authorize. Confirm no channel-level permission overwrite denies the bot these three permissions on the target channel.

## Get the channel ID
10. Discord client → User Settings → Advanced → toggle **Developer Mode** ON.
11. Right-click the target text channel → **Copy Channel ID**. Put it in `.env` as `COMMS_CHANNEL_ID`.
12. (Optional) Right-click your bot's posted prompt → **Copy Message ID** to seed the first `after=` cursor.

## How the bot reads replies (already implemented in `comms.read`)
- SEND: `POST https://discord.com/api/v10/channels/{channel_id}/messages` with `Authorization: Bot <TOKEN>` and `{"content": "..."}`.
- POLL (~every 30s): `GET .../messages?after={last_seen_id}&limit=100`. Persist the highest message `id` seen and pass it as the next `after`. Results are newest-first — reverse to process chronologically. Bot/webhook messages are filtered out.
- Honor rate limits: back off on HTTP 429 (`Retry-After`); a 30s interval on one channel is well within limits.
- Plain REST polling needs no Gateway/websocket and no `GUILD_MESSAGES` intent — only the Message Content Intent plus the View Channel + Read Message History permissions.

Once `DISCORD_BOT_TOKEN` and `COMMS_CHANNEL_ID` are in `.env`, `comms.inbound_enabled()` flips true and approvals can flow in from Discord.
