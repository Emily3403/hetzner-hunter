from typing import Any

from sqlalchemy import select, delete
from sqlalchemy.orm import Session as DatabaseSession

from hetzner_server_scouter.db.db_utils import add_object_to_database, add_objects_to_database
from hetzner_server_scouter.db.models import Server
from hetzner_server_scouter.notifications.crud import create_logs_from_changes
from hetzner_server_scouter.notifications.models import ServerChange, ServerChangeType, NotificationConfig, ServerChangeLog
from hetzner_server_scouter.notifications.notify_telegram import telegram_notify_about_changes
from hetzner_server_scouter.settings import get_hetzner_api
from hetzner_server_scouter.utils import get_message_id_from_change_log


def read_servers(db: DatabaseSession) -> list[Server]:
    return list(db.execute(select(Server)).scalars().all())


def read_servers_to_ids(db: DatabaseSession) -> dict[int, Server]:
    return {it.id: it for it in read_servers(db)}


def create_server_from_data(db: DatabaseSession, data: dict[str, Any], last_message_id: int | None = None) -> Server | None:
    return add_object_to_database(db, Server.from_data(data, last_message_id=last_message_id))


async def process_changes(db: DatabaseSession, config: NotificationConfig, updated_servers: list[tuple[Server, Server | None]], deleted_servers: list[Server]) -> None:
    updated_changes = [it for new, old in updated_servers if (it := new.process_change(old)) is not None]
    deleted_changes = [ServerChange(ServerChangeType.sold, it.id, it.to_dict(), {}) for it in deleted_servers]

    changes = updated_changes + deleted_changes
    logs = create_logs_from_changes(db, changes)
    if logs is None:
        return

    await telegram_notify_about_changes(db, logs, config)

    pass


async def download_server_list(db: DatabaseSession, config: NotificationConfig) -> list[Server] | None:
    api_data = get_hetzner_api()
    if api_data is None:
        return None

    existing_servers = read_servers_to_ids(db)
    servers_to_update: list[tuple[Server, Server | None]] = []

    for data in api_data["server"]:
        maybe_server = existing_servers.pop(data["id"], None)
        if maybe_server is None:
            server = Server.from_data(data)
        elif maybe_server.last_message_id is not None:
            server = Server.from_data(data, last_message_id=maybe_server.last_message_id)
        else:
            # This should be unreachable, but I can't explain the behaviour otherwise
            log: ServerChangeLog = sorted(maybe_server.change_logs, key=lambda it: it.time)[-1]  # type:ignore[unreachable]
            server = Server.from_data(data, last_message_id=get_message_id_from_change_log(log.change))

        if server is None or server == maybe_server:
            continue

        servers_to_update.append((server, maybe_server))

    # First delete all old servers
    server_ids = [it.id for (_, it) in servers_to_update if it is not None]
    db.execute(delete(Server).where(Server.id.in_(server_ids)))
    db.execute(delete(Server).where(Server.id.in_([it.id for it in existing_servers.values()])))

    # Next, insert all new ones and process the changes
    new_servers = add_objects_to_database(db, [it for (it, _) in servers_to_update])
    await process_changes(db, config, servers_to_update, list(existing_servers.values()))
    return new_servers
