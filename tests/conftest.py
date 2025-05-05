from datetime import datetime, timedelta

import pytest
import pytz

from esds_apps.classes import MembershipCard, MembershipCardCheck, MembershipCardStatus


@pytest.fixture
def sample_card():
    return MembershipCard(
        expires_at=datetime(2025, 12, 31),
        member_uuid='member-123',
        card_uuid='card-456',
        status=MembershipCardStatus.ISSUED,
        card_number='12345',
        first_name='Alice',
        last_name='Smith',
        email='alice@example.com',
    )


@pytest.fixture
def sample_check():
    now = datetime.now(pytz.timezone('Europe/London'))
    return MembershipCardCheck(
        member_uuid='uuid',
        card_uuid='card',
        card_number='123456',
        first_name='Alice',
        last_name='Smith',
        checked_at=now - timedelta(days=5),
        checked_by='admin',
    )
