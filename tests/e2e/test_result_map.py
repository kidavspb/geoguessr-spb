"""Карта результата держит точки в середине видимой части, не под панелью."""
import json
import math
import re

import pytest
from playwright.sync_api import expect

VIEWPORTS = {
    'desktop': {'width': 1280, 'height': 800},
    'mobile': {'width': 390, 'height': 844},
}


def _mercator_y(lat):
    rad = math.radians(max(-85, min(85, lat)))
    return (1 - math.log(math.tan(math.pi / 4 + rad / 2)) / math.pi) / 2


def _shortened_round(route):
    response = route.fetch()
    data = response.json()
    data['remaining_ms'] = 1500
    route.fulfill(response=response, json=data)


def _visible_area_center(page):
    """Центр части карты результата, не закрытой панелью."""
    return page.evaluate("""() => {
        const map = document.getElementById('result-map').getBoundingClientRect();
        const panel = document.querySelector('.result-panel').getBoundingClientRect();
        const panelOnRight = panel.left > map.left + 1;
        const right = panelOnRight ? panel.left : map.right;
        const bottom = panelOnRight ? map.bottom : Math.min(map.bottom, panel.top);
        return {
            x: (map.left + right) / 2,
            y: (map.top + bottom) / 2,
            mapX: map.left + map.width / 2,
            mapY: map.top + map.height / 2,
        };
    }""")


def _screen_position(page, lon, lat):
    """Где точка окажется при начальном виде карты (центр и zoom из location)."""
    location = json.loads(page.locator('#result-map').get_attribute('data-map-location'))
    center_lon, center_lat = location['center']
    world = 256 * 2 ** location['zoom']
    area = _visible_area_center(page)
    return (
        area['mapX'] + (lon - center_lon) / 360 * world,
        area['mapY'] + (_mercator_y(lat) - _mercator_y(center_lat)) * world,
        area,
    )


@pytest.mark.parametrize('viewport', VIEWPORTS)
@pytest.mark.parametrize('timed_out', [True, False])
def test_result_points_are_centered_in_visible_part_of_map(page, server, viewport, timed_out):
    page.set_viewport_size(VIEWPORTS[viewport])
    guesses = []
    page.on('response', lambda response: guesses.append(response.json())
            if response.url.endswith('/api/game/guess') else None)
    if timed_out:
        page.route('**/api/game/ready', _shortened_round)
    page.goto(server)
    if timed_out:
        page.get_by_text('1 мин', exact=True).click()
    page.locator('#start-btn').click()
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    if not timed_out:
        if viewport == 'mobile':
            page.locator('#map-handle').click()
        page.locator('#map').click(position={'x': 60, 'y': 60})
        page.locator('#guess-btn').click()
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'), timeout=15000)
    expect(page.locator('#result-map')).to_have_attribute('data-map-location', re.compile('center'))

    result = guesses[-1]
    correct = result['correct_location']
    if timed_out:
        assert result['guess'] is None
        lon, lat = correct['longitude'], correct['latitude']
    else:
        guess = result['guess']
        # Середина отрезка между точками в проекции карты.
        lon = (correct['longitude'] + guess['longitude']) / 2
        mid_y = (_mercator_y(correct['latitude']) + _mercator_y(guess['latitude'])) / 2
        lat = math.degrees(2 * math.atan(math.exp(math.pi * (1 - 2 * mid_y))) - math.pi / 2)

    x, y, area = _screen_position(page, lon, lat)
    assert abs(x - area['x']) < 3, (x, area)
    assert abs(y - area['y']) < 3, (y, area)
