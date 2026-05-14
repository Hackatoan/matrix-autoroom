# matrix-autoroom

AutoRoom bot for Matrix. Mirrors Discord's AutoRoom functionality — when someone sends a message in a configured generator room, the bot creates a temporary voice room under the same space category, invites the sender, and removes the room when it's empty.

## How it works

1. You designate certain rooms as **generators** (e.g. `#vip-generator`, `#crew-generator`)
2. Someone sends any message in a generator room (or `!room My Room Name` for a custom name)
3. The bot creates a new room under the generator's parent space and invites them
4. When the last member leaves, the bot tombstones and removes the room

## Setup

### 1. Create a bot account on your homeserver

```bash
curl -X POST https://matrix.hackatoa.com/_matrix/client/v3/register \
  -H "Content-Type: application/json" \
  -d '{"username":"autoroom","password":"yourpassword","auth":{"type":"m.login.dummy"}}'
```

Then log in to get an access token:

```bash
curl -X POST https://matrix.hackatoa.com/_matrix/client/v3/login \
  -H "Content-Type: application/json" \
  -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"autoroom"},"password":"yourpassword"}'
```

### 2. Invite the bot to your generator rooms and parent spaces

In Element, invite `@autoroom:matrix.hackatoa.com` to:
- Each generator room (`#vip-generator`, `#crew-generator`)
- Each parent space (Classified Sector, Docking Bay) — so it can add children

### 3. Configure

```bash
cp config.json.example config.json
```

Edit `config.json`:

```json
{
  "homeserver": "https://matrix.hackatoa.com",
  "user_id": "@autoroom:matrix.hackatoa.com",
  "access_token": "syt_...",
  "generators": {
    "vip-generator": "!CLASSIFIED_SECTOR_SPACE_ID:matrix.hackatoa.com",
    "crew-generator": "!DOCKING_BAY_SPACE_ID:matrix.hackatoa.com"
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

## Generator room usage

| Message | Result |
|---------|--------|
| Any message | Creates "Voice Room N" |
| `!room Gaming` | Creates "Gaming" |
