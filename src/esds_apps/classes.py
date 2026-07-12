from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import pytz

from esds_apps.config import DC_HOST

_UK_TZ = pytz.timezone('Europe/London')


class MembershipCardStatus(StrEnum):
    NEW = 'new'
    ISSUED = 'issued'
    EXPIRED = 'expired'
    CANCELLED = 'cancelled'
    DAMAGED = 'damaged'
    LOST = 'lost'
    STOLEN = 'stolen'


# Statuses that mean a card has been invalidated; the rest (new, issued) are current.
INVALIDATED_CARD_STATUSES = frozenset(
    {
        MembershipCardStatus.EXPIRED,
        MembershipCardStatus.CANCELLED,
        MembershipCardStatus.DAMAGED,
        MembershipCardStatus.LOST,
        MembershipCardStatus.STOLEN,
    }
)


def is_card_invalidated(status: 'MembershipCardStatus | None', expires_at: datetime | None) -> bool:
    """Whether a card should be treated as expired or invalidated.

    Dancecloud doesn't reliably flip an issued card's status to ``expired`` once its expiry date
    passes, so a lapsed expiry date counts as invalidated regardless of the stored status. Compared
    at date granularity to sidestep naive/aware datetime mismatches.
    """
    if status in INVALIDATED_CARD_STATUSES:
        return True
    if expires_at is not None and expires_at.date() < datetime.now(_UK_TZ).date():
        return True
    return False


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

    @property
    def is_invalidated(self) -> bool:
        """Whether this card is expired or otherwise invalidated."""
        return is_card_invalidated(self.status, self.expires_at)


@dataclass(frozen=True)
class MembershipCardCheck(_MembershipCommon):
    checked_at: datetime
    checked_by: str
    # Status and expiry of the checked card (may be absent if the card details weren't included).
    status: MembershipCardStatus | None = None
    expires_at: datetime | None = None

    @property
    def is_invalidated(self) -> bool:
        """Whether the checked card is expired or otherwise invalidated."""
        return is_card_invalidated(self.status, self.expires_at)


class PrintablePdfError(ValueError):
    """Raised when card layout settings exceed printable space."""

    pass
