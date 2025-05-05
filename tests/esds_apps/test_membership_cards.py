from email.message import EmailMessage
from unittest.mock import MagicMock, mock_open, patch

import pytest

from esds_apps.membership_cards import (
    compose_membership_email,
    generate_card_back_png,
    generate_card_front_png,
    mirror_page,
    printable_pdf,
)


@patch('esds_apps.membership_cards.etree.parse')
@patch('esds_apps.membership_cards.open', new_callable=mock_open, read_data=b'<svg></svg>')
@patch('esds_apps.membership_cards.segno.make')
@patch('esds_apps.membership_cards.cairosvg.svg2png')
def test_generate_card_front_png(mock_svg2png, mock_segno_make, mock_file, mock_parse, sample_card):
    mock_segno_instance = MagicMock()
    mock_segno_instance.svg_inline.return_value = '<svg>QR</svg>'
    mock_segno_instance.symbol_size.return_value = (21, 21)
    mock_segno_make.return_value = mock_segno_instance

    mock_background = MagicMock()
    mock_background.getroot.return_value = []
    mock_parse.return_value = mock_background

    mock_svg2png.return_value = b'FAKEPNGDATA'

    result = generate_card_front_png(sample_card)

    assert result == b'FAKEPNGDATA'
    mock_segno_make.assert_called_once()
    mock_svg2png.assert_called_once()


@patch('esds_apps.membership_cards.etree.parse')
@patch('esds_apps.membership_cards.open', new_callable=mock_open, read_data=b'<svg></svg>')
@patch('esds_apps.membership_cards.cairosvg.svg2png')
def test_generate_card_back_png(mock_svg2png, mock_file, mock_parse):
    mock_background = MagicMock()
    mock_background.getroot.return_value = []
    mock_parse.return_value = mock_background

    mock_svg2png.return_value = b'BACKPNGDATA'

    result = generate_card_back_png()

    assert result == b'BACKPNGDATA'
    mock_svg2png.assert_called_once()


def test_mirror_page_even():
    original = ['a', 'b', 'c', 'd']
    result = mirror_page(original, cards_per_row=2, cards_per_page=4)
    assert result == ['b', 'a', 'd', 'c']


def test_mirror_page_uneven():
    original = ['a', 'b']
    result = mirror_page(original, cards_per_row=2, cards_per_page=4)
    assert result == ['b', 'a', None, None]


@pytest.mark.asyncio
@patch('esds_apps.membership_cards.generate_card_front_png', return_value=b'FAKEPNG')
@patch('esds_apps.membership_cards.open', new_callable=mock_open, read_data=b'FAKEIMG')
@patch('esds_apps.membership_cards.config.TEMPLATES.env.get_template')
async def test_compose_membership_email(mock_get_template, mock_file, mock_generate_png, sample_card):
    mock_template = MagicMock()
    mock_template.render.return_value = '<html>Email</html>'
    mock_get_template.return_value = mock_template

    with patch(
        'esds_apps.membership_cards.json.load', return_value=[{'image_path': 'some/image.png', 'cid': 'img123'}]
    ):
        email_msg = await compose_membership_email(sample_card)

    assert isinstance(email_msg, EmailMessage)
    assert sample_card.email in email_msg['To']
    assert 'Your ESDS Membership' in email_msg['Subject']
    assert any(p.get_content_type() == 'multipart/alternative' for p in email_msg.iter_parts())
    assert any(p.get_filename() and p.get_filename().endswith('.png') for p in email_msg.iter_attachments())


@pytest.mark.asyncio
@patch('esds_apps.membership_cards.generate_card_front_png', return_value=b'front')
@patch('esds_apps.membership_cards.generate_card_back_png', return_value=b'back')
@patch('esds_apps.membership_cards.fetch_membership_cards')
@patch('esds_apps.membership_cards.config.TEMPLATES.get_template')
@patch('esds_apps.membership_cards.HTML')
async def test_printable_pdf_minimal_valid_case(  # noqa: PLR0913
    mock_html, mock_get_template, mock_fetch_cards, mock_back_png, mock_front_png, sample_card
):
    mock_fetch_cards.return_value = [sample_card]
    mock_template = MagicMock()
    mock_template.render.return_value = '<html>PDF</html>'
    mock_get_template.return_value = mock_template

    mock_html_instance = MagicMock()
    mock_html_instance.write_pdf.return_value = b'%PDF-fake'
    mock_html.return_value = mock_html_instance

    class DummyRequest:
        pass

    result = await printable_pdf(
        request=DummyRequest(),
        card_uuids=[sample_card.card_uuid],
        card_width_mm=85.6,
        card_height_mm=53.98,
        margin_top_mm=5,
        margin_left_mm=5,
        horizontal_gap_mm=5,
        vertical_gap_mm=5,
    )

    assert result.startswith(b'%PDF')
    assert mock_fetch_cards.called
    assert mock_html_instance.write_pdf.called
