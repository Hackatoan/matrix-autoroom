import asyncio
import logging
import json
import time
import uuid
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
JITSI_BASE_URL = "https://jitsi.hackatoa.com"

# Any message in a generator room triggers a room_create + several state
# writes + an invite. Without a limit, a single member (or a compromised/
# spammy client) can flood the generator with messages and exhaust the
# homeserver / space with junk rooms and invites. Guard with a per-sender
# cooldown and a per-generator cap.
ROOM_CREATE_COOLDOWN = 10  # seconds a sender must wait between triggering new rooms
MAX_ACTIVE_ROOMS_PER_GENERATOR = 20  # hard cap on concurrently active rooms per generator
last_created_at: dict = {}  # sender user_id → monotonic timestamp of last room-create trigger


async def create_temp_room(client: AsyncClient, creator: str, space_id: str, generator_alias: str, label: str, name_prefix: str = "Voice Room") -> Optional[str]:
    room_counters[generator_alias] = room_counters.get(generator_alias, 0) + 1
    n = room_counters[generator_alias]
    name = label or f"{name_prefix} {n}"

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
    add_to_space = client.room_put_state(
        space_id,
        "m.space.child",
        {"via": [client.user_id.split(":")[1]], "suggested": False},
        state_key=room_id,
    )

    # Add Jitsi voice widget so members can join a persistent voice call
    jitsi_room_name = room_id.lstrip("!").split(":")[0]
    jitsi_url = f"{JITSI_BASE_URL}/{jitsi_room_name}"
    widget_id = f"jitsi_{uuid.uuid4().hex[:8]}"
    add_jitsi_widget = client.room_put_state(
        room_id,
        "im.vector.modular.widgets",
        {
            "type": "jitsi",
            "url": f"{JITSI_BASE_URL}/widgets/jitsi.html?confId={jitsi_room_name}#confId=$conferenceId&domain=$domain&isAudioOnly=$isAudioOnly&displayName=$matrix_display_name&avatarUrl=$matrix_avatar_url&userId=$matrix_user_id&roomId=$matrix_room_id&theme=$theme",
            "name": "Voice",
            "data": {
                "domain": "jitsi.hackatoa.com",
                "conferenceId": jitsi_room_name,
                "isAudioOnly": False,
                "supportsScreensharing": True,
            },
            "creatorUserId": client.user_id,
            "id": widget_id,
        },
        state_key=widget_id,
    )

    # Invite creator
    invite_creator = client.room_invite(room_id, creator)

    # These three calls are independent of each other (all only need room_id,
    # already known) — run them concurrently instead of as sequential round-trips.
    await asyncio.gather(add_to_space, add_jitsi_widget, invite_creator)

    log.info("Created temp room %s (%s) with Jitsi voice for %s", name, room_id, creator)
    log.info("Jitsi URL: %s", jitsi_url)
    return room_id


async def remove_temp_room(client: AsyncClient, room_id: str):
    meta = active_rooms.get(room_id)
    if not meta:
        return

    # Removing from the space and tombstoning the room are independent of
    # each other (different rooms) — run them concurrently. room_leave must
    # come after the tombstone, since leaving revokes our power to set state.
    await asyncio.gather(
        client.room_put_state(
            meta["space"], "m.space.child", {}, state_key=room_id
        ),
        client.room_put_state(
            room_id,
            "m.room.tombstone",
            {"body": "This voice room has ended.", "replacement_room": meta["space"]},
        ),
    )

    await client.room_leave(room_id)
    del active_rooms[room_id]
    log.info("Removed temp room %s", room_id)


async def check_empty_rooms(client: AsyncClient):
    """Periodically check if any temp rooms have been empty for EMPTY_ROOM_TIMEOUT and remove them."""
    while True:
        await asyncio.sleep(60)
        try:
            now = time.monotonic()
            rooms_to_remove = []
            for room_id, meta in list(active_rooms.items()):
                room = client.rooms.get(room_id)
                if not room:
                    continue
                # Short-circuit on the first non-bot member instead of building
                # a full membership list just to test truthiness — room.users
                # can be large for bigger voice rooms, and only emptiness matters
                # here. meta already came from the items() iteration above, so
                # there's no need for a second active_rooms.get(room_id) lookup.
                has_members = any(m != client.user_id for m in room.users)
                if not has_members:
                    if meta["empty_since"] is None:
                        meta["empty_since"] = now
                        log.info("Room %s became empty, will remove in %ds", room_id, EMPTY_ROOM_TIMEOUT)
                    elif now - meta["empty_since"] >= EMPTY_ROOM_TIMEOUT:
                        log.info("Room %s empty for %ds, removing", room_id, EMPTY_ROOM_TIMEOUT)
                        rooms_to_remove.append(room_id)
                else:
                    if meta["empty_since"] is not None:
                        log.info("Room %s has members again, resetting empty timer", room_id)
                        meta["empty_since"] = None

            # Each timed-out room's teardown is independent (distinct room_id
            # and state_key) — run them concurrently instead of one at a time
            # per sweep.
            if rooms_to_remove:
                await asyncio.gather(*(remove_temp_room(client, rid) for rid in rooms_to_remove))
        except Exception:
            log.exception("Error while checking for empty rooms; will retry next cycle")


def make_message_callback(config: Config, client: AsyncClient):
    # Build reverse map: room_id → (space_id, generator_alias)
    # This is populated after the bot resolves aliases at startup
    generator_rooms: dict = {}  # room_id → (space_id, alias)

    async def resolve_generators():
        server_name = config.user_id.split(":")[1]
        # Each alias resolution is an independent HTTP round-trip — run them
        # concurrently instead of awaiting one at a time at startup.
        entries = [
            (alias, space_id, alias if alias.startswith("#") else f"#{alias}:{server_name}")
            for alias, space_id in config.generators.items()
        ]
        responses = await asyncio.gather(
            *(client.room_resolve_alias(full_alias) for _, _, full_alias in entries)
        )
        for (alias, space_id, full_alias), resp in zip(entries, responses):
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

        # Rate-limit per sender to stop message spam from triggering unbounded
        # room creation (resource exhaustion / invite spam).
        now = time.monotonic()
        last = last_created_at.get(event.sender)
        if last is not None and now - last < ROOM_CREATE_COOLDOWN:
            log.info(
                "Ignoring room-create trigger from %s in %s: cooldown active (%.1fs left)",
                event.sender, alias, ROOM_CREATE_COOLDOWN - (now - last),
            )
            return

        # Hard cap on concurrently active rooms per generator, independent of
        # cooldown, so a slow drip of messages can't grow it unbounded either.
        active_for_generator = sum(
            1 for meta in active_rooms.values() if meta.get("generator") == alias
        )
        if active_for_generator >= MAX_ACTIVE_ROOMS_PER_GENERATOR:
            log.warning(
                "Generator %s at capacity (%d active rooms); refusing to create another for %s",
                alias, MAX_ACTIVE_ROOMS_PER_GENERATOR, event.sender,
            )
            return

        last_created_at[event.sender] = now

        # Support optional custom name: "!room Gaming" or just any message triggers
        label = ""
        if body.startswith("!room "):
            label = body[6:].strip()

        new_room_id = await create_temp_room(client, event.sender, space_id, alias, label, config.room_name_prefix)
        if new_room_id:
            jitsi_room_name = new_room_id.lstrip("!").split(":")[0]
            jitsi_url = f"{JITSI_BASE_URL}/{jitsi_room_name}"
            await client.room_send(
                room.room_id,
                "m.room.message",
                {
                    "msgtype": "m.notice",
                    "body": (
                        f"Created voice room for {event.sender}.\n"
                        f"Matrix room: https://element.hackatoa.com/#/room/{new_room_id}\n"
                        f"Voice: {jitsi_url}"
                    ),
                },
            )
        else:
            # Room creation failed silently before this fix — the requester saw
            # no response at all and had no way to tell a failure from a slow
            # bot. Give explicit feedback so they know to retry or ask an admin.
            await client.room_send(
                room.room_id,
                "m.room.message",
                {
                    "msgtype": "m.notice",
                    "body": (
                        f"Sorry {event.sender}, I couldn't create a voice room "
                        "just now. Please try again in a moment, or ask an admin "
                        "if this keeps happening."
                    ),
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

    # Long-poll sync loop (full state was already fetched by the initial sync() above)
    await client.sync_forever(timeout=30000)


if __name__ == "__main__":
    asyncio.run(main())
