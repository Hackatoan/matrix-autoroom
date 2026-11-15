# matrix-autoroom

A Matrix bot that creates temporary voice rooms on demand and removes them when empty.

☕ **Support:** [Buy Me a Coffee](https://buymeacoffee.com/hackatoa)

## Overview

Keeps a Matrix space tidy by spinning up temporary voice rooms when needed and cleaning them up automatically once everyone leaves.

## Features

- On-demand temporary voice rooms
- Auto-removal when a room goes empty
- Self-hosted
- Tunable empty-room timeout, per-user cooldown and per-generator room cap via `config.json`

## Tech Stack

Python · Matrix (matrix-nio)

## Development

```bash
pip install -r requirements.txt
# configure your homeserver + access token, then run the bot
python3 bot.py
```

### Optional tuning

These `config.json` keys are optional; leave them out to keep the defaults.
Values must be positive integers (invalid values are logged and ignored).

| Key | Default | Meaning |
|-----|---------|---------|
| `empty_room_timeout` | `3600` | Seconds a temp room may sit empty before it is removed |
| `room_create_cooldown` | `10` | Seconds a user must wait between creating rooms |
| `max_rooms_per_generator` | `20` | Max concurrently active rooms per generator |

## Support

If this project is useful to you, consider supporting development:

☕ **[Buy Me a Coffee](https://buymeacoffee.com/hackatoa)**

---

Part of the **[Hackatoa](https://hackatoa.com)** ecosystem — self-hosted apps, browser games, and bots. · [All repositories »](https://github.com/Hackatoan)
