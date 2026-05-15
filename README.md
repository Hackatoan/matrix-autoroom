[![Buy Me A Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/hackatoa)

# matrix-autoroom

AutoRoom bot for Matrix. Mirrors Discord's AutoRoom — when someone sends a message in a configured generator room, the bot creates a temporary room under the same space, invites the sender, and removes the room when it's empty.

## How it works

1. Designate certain rooms as **generators** (e.g. `#vip-generator`)
2. Someone sends any message in a generator room — or `!room My Room Name` for a custom name
3. The bot creates a new room under the generator's parent space and invites them
4. When the last member leaves, the bot tombstones and removes the room

## Setup

### 1. Create a bot account

```bash
curl -X POST https://matrix.yourdomain.com/_matrix/client/v3/login \
  -H "Content-Type: application/json" \
  -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"autoroom"},"password":"yourpassword"}'
```

Save the returned `access_token`.

### 2. Invite the bot

In Element, invite `@autoroom:yourdomain.com` to:
- Each generator room
- Each parent space (so it can add children)

### 3. Configure

```bash
cp config.json.example config.json
```

```json
{
  "homeserver": "https://matrix.yourdomain.com",
  "user_id": "@autoroom:yourdomain.com",
  "access_token": "syt_...",
  "generators": {
    "vip-generator": "!SPACE_ROOM_ID:yourdomain.com"
  }
}
```

Get space room IDs in Element: open the space → Settings → Advanced.

### 4. Run

```bash
docker compose up -d
```

Or without Docker:

```bash
pip install -r requirements.txt
python bot.py
```

## Generator room commands

| Message | Result |
|---|---|
| Any message | Creates "Voice Room N" |
| `!room Gaming` | Creates "Gaming" |

---

[hackatoa.com](https://hackatoa.com) · [GitHub](https://github.com/Hackatoan) · [Buy Me A Coffee](https://buymeacoffee.com/hackatoa)
