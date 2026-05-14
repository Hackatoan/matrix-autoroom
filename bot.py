import asyncio
import logging
import json
import time
from dataclasses import dataclass, field
from typing import Optional
from nio import (
    AsyncClient, RoomMessageText, MatrixRoom, InviteMemberEvent,
    RoomMemberEvent, AsyncClientConfig
)
from nio.responses import JoinError, RoomCreateError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("autoroom")


@dataclass
class Config:
    homeserver: str
    user_id: str
    access_token: str
    # Maps generator room alias → parent space room_id
    generators: dict = field(default_factory=dict)
    room_name_prefix: str = "Voice Room"


# Track temp rooms: room_id → {"creator": user_id, "space": space_id, "number": int, "empty_since": float|None}
active_rooms: dict = {}
room_counters: dict = {}  # generator_alias → next int
EMPTY_ROOM_TIMEOUT = 3600  # seconds before an empty room is removed


async def create_temp_room(client: AsyncClient, creator: str, space_id: str, generator_alias: str, label: str) -> Optional[str]:
    room_counters[generator_alias] = room_counters.get(generator_alias, 0) + 1
    n = room_counters[generator_alias]
    name = label or f"Voice Room {n}"

    resp = await client.room_create(
        name=name,
        initial_state=[
            {"type": "m.room.history_visibility", "content": {"history_visibility": "shared"}},
            {"type": "m.room.power_levels", "content": {"users_default": 0, "users": {client.user_id: 100}}},
        ],
    )

    if isinstance(resp, RoomCreateError):
        log.error("Failed to create temp room: %s", resp)
        return None

    room_id = resp.room_id
    active_rooms[room_id] = {"creator": creator, "space": space_id, "generator": generator_alias, "empty_since": None}

    # Add to parent space
    await client.room_put_state(
        space_id,
        "m.space.child",
        {"via": [client.user_id.split(":")[1]], "suggested": False},
        state_key=room_id,
    )

    # Invite creator
    await client.room_invite(room_id, creator)

    log.info("Created temp room %s (%s) for %s", name, room_id, creator)
    return room_id


async def remove_temp_room(client: AsyncClient, room_id: str):
    meta = active_rooms.get(room_id)
    if not meta:
        return

    # Remove from parent space
    await client.room_put_state(
        meta["space"], "m.space.child", {}, state_key=room_id
    )

    # Tombstone the room pointing back to the generator
    await client.room_put_state(
        room_id,
        "m.room.tombstone",
        {"body": "This voice room has ended.", "replacement_room": meta["space"]},
    )

    await client.room_leave(room_id)
    del active_rooms[room_id]
    log.info("Removed temp room %s", room_id)


async def check_empty_rooms(client: AsyncClient):
    """Periodically check if any temp rooms have been empty for EMPTY_ROOM_TIMEOUT and remove them."""
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        for room_id in list(active_rooms.keys()):
            room = client.rooms.get(room_id)
            if not room:
                continue
            members = [m for m in room.users if m != client.user_id]
            meta = active_rooms.get(room_id)
            if not meta:
                continue
            if not members:
                if meta["empty_since"] is None:
                    meta["empty_since"] = now
                    log.info("Room %s became empty, will remove in %ds", room_id, EMPTY_ROOM_TIMEOUT)
                elif now - meta["empty_since"] >= EMPTY_ROOM_TIMEOUT:
                    log.info("Room %s empty for %ds, removing", room_id, EMPTY_ROOM_TIMEOUT)
                    await remove_temp_room(client, room_id)
            else:
                if meta["empty_since"] is not None:
                    log.info("Room %s has members again, resetting empty timer", room_id)
                    meta["empty_since"] = None


def make_message_callback(config: Config, client: AsyncClient):
    # Build reverse map: room_id → (space_id, generator_alias)
    # This is populated after the bot resolves aliases at startup
    generator_rooms: dict = {}  # room_id → (space_id, alias)

    async def resolve_generators():
        for alias, space_id in config.generators.items():
            server_name = config.user_id.split(":")[1]
            full_alias = alias if alias.startswith("#") else f"#{alias}:{server_name}"
            resp = await client.room_resolve_alias(full_alias)
            if hasattr(resp, "room_id"):
                generator_rooms[resp.room_id] = (space_id, alias)
                log.info("Generator %s → %s (space: %s)", full_alias, resp.room_id, space_id)
            else:
                log.warning("Could not resolve generator alias %s: %s", full_alias, resp)

    async def on_message(room: MatrixRoom, event: RoomMessageText):
        if event.sender == client.user_id:
            return
        if room.room_id not in generator_rooms:
            return

        space_id, alias = generator_rooms[room.room_id]
        body = event.body.strip()

        # Support optional custom name: "!room Gaming" or just any message triggers
        label = ""
        if body.startswith("!room "):
            label = body[6:].strip()

        new_room_id = await create_temp_room(client, event.sender, space_id, alias, label)
        if new_room_id:
            await client.room_send(
                room.room_id,
                "m.room.message",
                {
                    "msgtype": "m.notice",
                    "body": f"Created voice room for {event.sender}. Join: https://element.hackatoa.com/#/room/{new_room_id}",
                },
            )

    # Attach resolver so it runs at startup
    on_message._resolve = resolve_generators
    on_message._generator_rooms = generator_rooms
    return on_message


async def main():
    with open("config.json") as f:
        raw = json.load(f)

    config = Config(
        homeserver=raw["homeserver"],
        user_id=raw["user_id"],
        access_token=raw["access_token"],
        generators=raw.get("generators", {}),
        room_name_prefix=raw.get("room_name_prefix", "Voice Room"),
    )

    client = AsyncClient(
        config.homeserver,
        config.user_id,
        config=AsyncClientConfig(max_limit_exceeded=0, max_timeouts=0),
    )
    client.access_token = config.access_token
    client.user_id = config.user_id

    msg_callback = make_message_callback(config, client)
    client.add_event_callback(msg_callback, RoomMessageText)

    log.info("Starting AutoRoom bot as %s", config.user_id)

    # Initial sync to get room state
    await client.sync(timeout=5000)

    # Resolve generator aliases after first sync
    await msg_callback._resolve()

    # Start background empty-room reaper
    asyncio.create_task(check_empty_rooms(client))

    # Long-poll sync loop
    await client.sync_forever(timeout=30000, full_state=True)


if __name__ == "__main__":
    asyncio.run(main())
