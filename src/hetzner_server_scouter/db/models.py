from __future__ import annotations

from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import Text
from sqlalchemy.orm import mapped_column, Mapped, composite
from sqlalchemy_utils import JSONType

from hetzner_server_scouter.db.db_conf import DataBase
from hetzner_server_scouter.settings import Datacenters, ServerSpecials
from hetzner_server_scouter.utils import datetime_nullable_fromtimestamp, program_args, hetzner_ipv4_price

if TYPE_CHECKING:
    from hetzner_server_scouter.notify.models import ServerChange


class Server(DataBase):  # type:ignore[valid-type, misc]
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    last_message_id: Mapped[int] = mapped_column(unique=True, nullable=True, default=None)

    price: Mapped[float] = mapped_column(nullable=False)
    time_of_next_price_reduce: Mapped[datetime | None] = mapped_column(nullable=True)
    datacenter: Mapped[Datacenters] = mapped_column(nullable=False)
    cpu_name: Mapped[str] = mapped_column(Text, nullable=False)

    ram_size: Mapped[int] = mapped_column(nullable=False)
    ram_num: Mapped[int] = mapped_column(nullable=False)

    hdd_disks: Mapped[list[int]] = mapped_column(JSONType)
    sata_disks: Mapped[list[int]] = mapped_column(JSONType)
    nvme_disks: Mapped[list[int]] = mapped_column(JSONType)

    specials: Mapped[ServerSpecials] = composite(
        mapped_column("has_ipv4"), mapped_column("has_gpu"), mapped_column("has_inic"), mapped_column("has_ecc"), mapped_column("has_hwr")
    )

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> Server | None:
        from hetzner_server_scouter.utils import filter_server_with_program_args

        return filter_server_with_program_args(
            Server(
                id=data["id"], price=data["price"], time_of_next_price_reduce=datetime_nullable_fromtimestamp(None if data["fixed_price"] else data["next_reduce_timestamp"]), datacenter=Datacenters.from_data(data["datacenter"]),
                cpu_name=data["cpu"], ram_size=data["ram_size"], ram_num=int(data["ram"][0][0]), hdd_disks=data["serverDiskData"]["hdd"], sata_disks=data["serverDiskData"]["sata"], nvme_disks=data["serverDiskData"]["nvme"],
                specials=ServerSpecials("IPv4" in data["specials"], "GPU" in data["specials"], "iNIC" in data["specials"], "ECC" in data["specials"], "HWR" in data["specials"])
            )
        )

    def to_dict(self) -> dict[str, Any]:
        ret: dict[str, Any] = {}

        for key, item in self.__dict__.items():
            if key.startswith("_sa"):
                continue

            if isinstance(item, ServerSpecials):
                ret[key] = item.__dict__
            elif isinstance(item, Datacenters):
                ret[key] = item.value
            elif isinstance(item, datetime):
                ret[key] = item.isoformat()
            else:
                ret[key] = item

        return ret

    def __eq__(self, other: object | Server) -> bool:
        if not isinstance(other, Server):
            return False

        return self.id == other.id and self.price == other.price and self.datacenter == other.datacenter and self.cpu_name == other.cpu_name and self.ram_size == other.ram_size and \
            self.ram_num == other.ram_num and self.hdd_disks == other.hdd_disks and self.sata_disks == other.sata_disks and self.nvme_disks == other.nvme_disks and self.specials == other.specials

    def process_change(self, old: Server | None) -> ServerChange | None:
        from hetzner_server_scouter.notify.models import ServerChange, ServerChangeType

        if old is None:
            return ServerChange(ServerChangeType.new, self.id, {}, self.to_dict())

        if self.price != old.price:
            return ServerChange(ServerChangeType.price_changed, self.id, old.to_dict(), self.to_dict())

        if any(getattr(self, attr) != getattr(old, attr) for attr in ["datacenter", "cpu_name", "ram_size", "ram_num", "hdd_disks", "sata_disks", "nvme_disks", "specials"]):
            return ServerChange(ServerChangeType.hardware_changed, self.id, old.to_dict(), self.to_dict())

        return None

    def calculate_price(self) -> float:
        return self._calculate_price(self.price, self.specials.has_IPv4)

    @staticmethod
    def _calculate_price(price: float, has_ipv4: bool) -> float:
        return float(price * (1 + program_args.tax / 100) + (hetzner_ipv4_price or 0) * has_ipv4)
