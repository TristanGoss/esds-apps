from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MembershipCardStatus(StrEnum):
    NEW = 'new'
    ISSUED = 'issued'
    EXPIRED = 'expired'
    CANCELLED = 'cancelled'
    DAMAGED = 'damaged'
    LOST = 'lost'
    STOLEN = 'stolen'


@dataclass(frozen=True)
class MembershipCard:
    card_uuid: str
    member_uuid: str
    card_number: int
    expires_at: datetime
    first_name: str
    last_name: str
    email: str
    status: MembershipCardStatus


class PrintablePdfError(ValueError):
    """Raised when card layout settings exceed printable space."""

    pass
