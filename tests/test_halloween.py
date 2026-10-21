"""Хэллоуинский режим: календарное окно и выключатель HALLOWEEN_MODE."""
from datetime import date

import pytest

from halloween import halloween_active


@pytest.mark.parametrize('day, expected', [
    (date(2026, 10, 19), False),
    (date(2026, 10, 20), True),
    (date(2026, 10, 31), True),
    (date(2026, 11, 1), True),
    (date(2026, 11, 2), False),
    (date(2027, 6, 1), False),
    (date(2027, 10, 25), True),
])
def test_calendar_window_repeats_every_year(day, expected):
    assert halloween_active('', today=day) is expected
    assert halloween_active('auto', today=day) is expected


@pytest.mark.parametrize('mode', ['on', 'ON', ' true ', '1', 'yes'])
def test_mode_on_forces_season(mode):
    assert halloween_active(mode, today=date(2026, 6, 1)) is True


@pytest.mark.parametrize('mode', ['off', 'False', '0', 'no'])
def test_mode_off_disables_season(mode):
    assert halloween_active(mode, today=date(2026, 10, 31)) is False


@pytest.mark.parametrize('mode, visible', [('on', True), ('off', False)])
def test_index_renders_chase_only_in_season(app_module, client, monkeypatch, mode, visible):
    monkeypatch.setattr(app_module, 'HALLOWEEN_MODE', mode)
    html = client.get('/').get_data(as_text=True)
    for marker in ('<body class="halloween">', 'id="chase-btn"', 'id="chase-layer"',
                   'id="chase-caught"', '<symbol id="horseman"'):
        assert (marker in html) is visible, marker
