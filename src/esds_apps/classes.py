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
class MembershipCard:
    card_uuid: str
    member_uuid: str
    card_number: int
    expires_at: datetime
    first_name: str
    last_name: str
    email: str
    status: MembershipCardStatus

    @property
    def check_url(self) -> str:
        """URL for the QR code on the membership card."""
        return f'{DC_HOST}/members/cards/{self.card_uuid}/check'


class PrintablePdfError(ValueError):
    """Raised when card layout settings exceed printable space."""

    pass
