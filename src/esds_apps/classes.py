from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from esds_apps.config import DC_HOST


class MembershipCardStatus(StrEnum):
    NEW = 'new'
    ISSUED = 'issued'
    EXPIRED = 'expired'
    CANCELLED = 'cancelled'
    DAMAGED = 'damaged'
    LOST = 'lost'
    STOLEN = 'stolen'


@dataclass(frozen=True)
class _Person:
    first_name: str
    last_name: str


@dataclass(frozen=True)
class _MembershipCommon(_Person):
    card_uuid: str
    member_uuid: str
    card_number: int


@dataclass(frozen=True)
class DoorVolunteer(_Person):
    volunteer_uuid: str
    email: str


@dataclass(frozen=True)
class MembershipCard(_MembershipCommon):
    email: str
    status: MembershipCardStatus
    expires_at: datetime

    @property
    def check_url(self) -> str:
        """URL for the QR code on the membership card."""
        return f'{DC_HOST}/members/cards/{self.card_uuid}/check'


@dataclass(frozen=True)
class MembershipCardCheck(_MembershipCommon):
    checked_at: datetime
    checked_by: str


class PrintablePdfError(ValueError):
    """Raised when card layout settings exceed printable space."""

    pass
