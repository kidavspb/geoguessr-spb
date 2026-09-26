"""E2E: полный игровой цикл в реальном браузере (Chromium + заглушка карт).

Ловит регрессии фронтенда, которые не видны юнит-тестам бэкенда:
несуществующие элементы, сломанные обработчики, порядок экранов.
"""
import re

import pytest

try:
    import playwright  # noqa: F401
except ImportError:
    pytest.skip('playwright не установлен', allow_module_level=True)

from playwright.sync_api import expect


def test_feedback_link_is_only_on_start_and_fits_mobile(page, server):
    page.goto(server)
    link = page.get_by_role('link', name='Напиши нам в Telegram')
    expect(link).to_have_count(1)
    expect(page.locator('#start-screen .start-feedback a')).to_have_count(1)
    expect(link).to_have_attribute('href', 'https://t.me/geoguessr_spb_ru')
    expect(link).to_have_attribute('rel', 'noopener noreferrer')
    for width in [320, 375, 390, 430, 1280]:
        page.set_viewport_size({'width': width, 'height': 844})
        link.scroll_into_view_if_needed()
        expect(link).to_be_visible()
        assert link.bounding_box()['height'] >= 44
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    link.focus()
    expect(link).to_be_focused()


def _play_round(page):
    """Дождаться панорамы, кликнуть по карте, ответить."""
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)

    page.locator('#map').click(position={'x': 150, 'y': 120})
    expect(page.locator('#guess-btn')).to_be_enabled()
    page.locator('#guess-btn').click()

    expect(page.locator('#result-screen')).to_have_class(re.compile('active'), timeout=10000)


@pytest.mark.parametrize('district_id', ['kronshtadtsky', 'pushkinsky', 'tsentralny'])
@pytest.mark.parametrize('width', [390, 1280])
def test_guess_map_starts_at_district_and_resets_each_round(page, server, district_id, width):
    from districts import district_map
    page.set_viewport_size({'width': width, 'height': 844})
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option(district_id)
    page.locator('#district-confirm-btn').click()
    page.locator('#start-btn').click()
    expect(page.locator('#map')).to_have_attribute('data-map-location', re.compile('center'))
    location = page.locator('#map').evaluate('el => JSON.parse(el.dataset.mapLocation)')
    west, south, east, north = district_map()[district_id].bounds
    assert location['center'][0] == pytest.approx((west + east) / 2)
    assert south < location['center'][1] < north
    default_zoom = page.evaluate("async () => (await import('/static/js/state.js')).DEFAULT_ZOOM")
    assert default_zoom <= location['zoom'] <= default_zoom + 1
    if district_id in ('kronshtadtsky', 'pushkinsky'):
        assert location['zoom'] == default_zoom
    page.evaluate("""async () => {
        const { state } = await import('/static/js/state.js');
        state.map.setLocation({center: [0, 0], zoom: 3});
    }""")
    if width <= 720:
        page.locator('#map-handle').click()
    _play_round(page)
    page.locator('#next-round-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible()
    reset = page.locator('#map').evaluate('el => JSON.parse(el.dataset.mapLocation)')
    assert reset['center'] == pytest.approx(location['center'])
    assert default_zoom <= reset['zoom'] <= default_zoom + 1
    standard = page.evaluate("""async () => {
        const { state, SPB_CENTER, DEFAULT_ZOOM } = await import('/static/js/state.js');
        const { resetMapForNewRound } = await import('/static/js/maps.js');
        state.gameData.difficulty = 'medium';
        resetMapForNewRound();
        return {
            actual: JSON.parse(document.getElementById('map').dataset.mapLocation),
            expected: {center: [SPB_CENTER[1], SPB_CENTER[0]], zoom: DEFAULT_ZOOM}
        };
    }""")
    assert standard['actual'] == standard['expected']


def test_next_round_does_not_expand_map_under_stationary_pointer(page, server):
    page.set_viewport_size({'width': 1280, 'height': 844})
    page.goto(server)
    page.locator('#start-btn').click()
    _play_round(page)
    # Воспроизводим совпадение кнопки результата с областью маленькой карты.
    page.locator('#next-round-btn').evaluate("""el => {
        Object.assign(el.style, {
            position: 'fixed', right: '40px', bottom: '120px',
            width: '200px', zIndex: '1000'
        });
    }""")
    page.locator('#next-round-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible()
    expect(page.locator('#photo-overlay')).to_be_hidden()
    panel = page.locator('#map-panel')
    expect(panel).to_have_css('width', '340px')
    expect(page.locator('.map-wrap')).to_have_css('height', '220px')
    assert panel.evaluate("el => el.matches(':hover')")
    # Движение внутри карты ещё не означает новое наведение.
    page.mouse.move(1150, 690)
    expect(panel).to_have_css('width', '340px')
    page.mouse.move(0, 0)
    page.locator('#map').hover()
    expect(panel).to_have_css('width', '560px')
    page.mouse.move(0, 0)
    expect(panel).to_have_css('width', '340px')
    # Клавиатурный доступ остаётся рабочим даже при заблокированном hover.
    page.evaluate("""async () => {
        const {resetMapForNewRound} = await import('/static/js/maps.js');
        resetMapForNewRound();
        const map = document.getElementById('map');
        map.tabIndex = 0;
        map.focus();
    }""")
    expect(panel).to_have_css('width', '560px')


def test_native_touch_pinch_keeps_page_still_and_single_finger_scrolls(page, server):
    page.set_viewport_size({'width': 390, 'height': 560})
    page.goto(server)
    page.locator('#district-picker-btn').click()
    svg = page.locator('#district-map')
    expect(svg).to_be_visible()
    svg.scroll_into_view_if_needed()
    session = page.context.new_cdp_session(page)
    session.send('Emulation.setTouchEmulationEnabled', {'enabled': True, 'maxTouchPoints': 5})
    rect = svg.bounding_box()
    x, y = rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2
    before = _viewbox(page)
    scroll = page.evaluate('scrollY')

    def send(kind, points):
        session.send('Input.dispatchTouchEvent', {'type': kind, 'touchPoints': [
            {'id': i, 'x': px, 'y': py} for i, (px, py) in enumerate(points)
        ]})
        page.wait_for_timeout(30)

    send('touchStart', [(x - 25, y)])
    send('touchStart', [(x - 25, y), (x + 25, y)])
    for step in range(1, 7):
        send('touchMove', [(x - 25 - step * 8, y - step * 3),
                           (x + 25 + step * 8, y - step * 3)])
    send('touchEnd', [])
    assert _viewbox(page)[2] < before[2] / 2
    assert page.evaluate('scrollY') == pytest.approx(scroll, abs=1)
    page.locator('#district-map-reset').click()
    svg.scroll_into_view_if_needed()
    rect = svg.bounding_box()
    x, y = rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2
    scroll = page.evaluate('scrollY')
    send('touchStart', [(x, y)])
    for step in range(1, 7):
        send('touchMove', [(x, y - step * 12)])
    send('touchEnd', [])
    assert page.evaluate('scrollY') > scroll + 10
    expect(svg).not_to_have_class(re.compile('is-zoomed'))
    session.detach()


def test_district_reset_focus_depends_on_input_method(page, server):
    page.goto(server)
    page.locator('#district-picker-btn').click()
    svg = page.locator('#district-map')
    expect(svg).to_be_visible()
    district = page.locator('#district-map .district-shape[tabindex="0"]')
    reset = page.locator('#district-map-reset')

    def zoom():
        svg.dispatch_event('wheel', {'deltaY': -400, 'clientX': 200, 'clientY': 200})
        expect(reset).to_be_visible()

    zoom()
    reset.click()
    assert not svg.evaluate('el => el.contains(document.activeElement)')
    expect(page.locator('#district-selected-name')).to_have_text('Район не выбран')
    zoom()
    reset.focus()
    reset.press('Enter')
    expect(district).to_be_focused()
    expect(reset).to_be_hidden()
    # iOS может оставить старый фокус на polygon после касания кнопки.
    zoom()
    district.focus()
    reset.dispatch_event('click', {'detail': 1})
    expect(district).not_to_be_focused()
    expect(page.locator('#district-selected-name')).to_have_text('Район не выбран')


def _viewbox(page):
    """Текущий SVG viewBox как четыре числа."""
    value = page.locator('#district-map').get_attribute('viewBox')
    assert value is not None
    parts = tuple(float(part) for part in value.split())
    assert len(parts) == 4
    return parts


def _assert_viewbox_inside(view, outer, tolerance=0.05):
    """Pan оставляет zoomed viewport внутри исходного fit."""
    x, y, width, height = view
    outer_x, outer_y, outer_width, outer_height = outer
    assert width <= outer_width + tolerance
    assert height <= outer_height + tolerance
    assert x >= outer_x - tolerance
    assert y >= outer_y - tolerance
    assert x + width <= outer_x + outer_width + tolerance
    assert y + height <= outer_y + outer_height + tolerance


def _color_alpha(value):
    """Alpha из browser-normalized rgb()/rgba()."""
    components = re.findall(r'[\d.]+', value)
    return float(components[3]) if len(components) >= 4 else 1.0


def test_start_settings_are_semantic_and_responsive(page, server):
    """Настройки работают как шкала, presets и switch, включая клавиатуру."""
    console_issues = []
    failed_resources = []
    page.on('pageerror', lambda error: console_issues.append(str(error)))
    page.on('console', lambda message: console_issues.append(message.text)
            if message.type in ('warning', 'error')
            and not message.text.startswith('Failed to load resource:') else None)
    page.on('response', lambda response: failed_resources.append(response.url)
            if response.status >= 400 else None)
    page.set_viewport_size({'width': 320, 'height': 780})
    page.goto(server)

    # Нативная семантика доступна не только мыши: range, radio group, switch.
    territory = page.get_by_role('slider', name='Территория')
    expect(territory).to_have_value('1')
    expect(territory).to_have_attribute('aria-valuetext', 'Средняя')
    expect(page.locator('#territory-value')).to_have_text('Средняя')
    expect(page.get_by_role('radio', name='Без лимита')).to_be_checked()
    movement = page.get_by_role('switch')
    expect(movement).to_be_checked()
    expect(page.locator('#movement-title')).to_have_text('Можно перемещаться')
    assert page.evaluate(
        "getComputedStyle(document.documentElement).backgroundColor"
    ) == 'rgb(35, 28, 98)'

    # Обычные reload получают согласованные HTML/CSS/JS без падения initToggles.
    for _ in range(3):
        page.reload()
        expect(territory).to_have_value('1')
        expect(page.locator('#territory-value')).to_have_text('Средняя')

    # Клик по каждой подписи выбирает соответствующую дискретную позицию.
    for label, value in [('Центр', '0'), ('Средняя', '1'), ('Весь город', '2')]:
        page.get_by_role('button', name=label, exact=True).click()
        expect(territory).to_have_value(value)
        expect(page.locator('#territory-value')).to_have_text(label)
        expect(page.get_by_role('button', name=label, exact=True)).to_have_attribute(
            'aria-pressed', 'true')

    # Сам track кликается, thumb перетаскивается и всегда snap'ится к шагу.
    box = territory.bounding_box()
    page.mouse.click(box['x'] + 2, box['y'] + box['height'] / 2)
    expect(territory).to_have_value('0')
    page.mouse.move(box['x'] + 10, box['y'] + box['height'] / 2)
    page.mouse.down()
    page.mouse.move(box['x'] + box['width'] - 10, box['y'] + box['height'] / 2)
    page.mouse.up()
    expect(territory).to_have_value('2')
    assert territory.evaluate(
        "element => getComputedStyle(element).webkitTapHighlightColor"
    ) == 'rgba(0, 0, 0, 0)'

    # Клавиши range и radio сохраняют ожидаемый порядок слева направо.
    territory.focus()
    page.keyboard.press('Home')
    expect(territory).to_have_value('0')
    assert territory.evaluate("element => element.matches(':focus-visible')")
    page.keyboard.press('ArrowRight')
    expect(territory).to_have_value('1')
    page.keyboard.press('End')
    expect(territory).to_have_value('2')

    page.get_by_text('1 мин', exact=True).click()
    expect(page.get_by_role('radio', name='1 мин')).to_be_checked()
    page.get_by_text('3 мин', exact=True).click()
    expect(page.get_by_role('radio', name='3 мин')).to_be_checked()
    page.get_by_role('radio', name='3 мин').focus()
    page.keyboard.press('ArrowRight')
    expect(page.get_by_role('radio', name='Без лимита')).to_be_checked()

    movement.click()
    expect(movement).not_to_be_checked()
    expect(page.locator('#movement-title')).to_have_text('Нельзя перемещаться')
    expect(page.locator('#movement-description')).to_have_text(
        'Начальная точка обзора зафиксирована')
    expect(page.locator('.move-emoji-fixed')).to_be_visible()
    assert movement.evaluate(
        "element => getComputedStyle(element).webkitTapHighlightColor"
    ) == 'rgba(0, 0, 0, 0)'
    movement.focus()
    page.keyboard.press('Space')
    expect(movement).to_be_checked()
    expect(page.locator('#movement-title')).to_have_text('Можно перемещаться')
    expect(page.locator('#movement-description')).to_have_text(
        'Свободно перемещайтесь по панораме')
    expect(page.locator('.move-emoji-walk')).to_be_visible()

    # Компактные подписи остаются удобны благодаря широким зонам по горизонтали;
    # основные controls сохраняют touch targets не меньше 44px.
    territory_label_sizes = page.locator('.territory-label').evaluate_all(
        "elements => elements.map(el => el.getBoundingClientRect())")
    assert all(size['height'] >= 32 for size in territory_label_sizes)
    control_sizes = page.locator(
        '.segmented-control label, .move-setting'
    ).evaluate_all("elements => elements.map(el => el.getBoundingClientRect())")
    assert all(size['height'] >= 44 for size in control_sizes)
    for width, height in [(320, 780), (390, 844), (430, 860), (1280, 800)]:
        page.set_viewport_size({'width': width, 'height': height})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

    # Основная комбинация доходит до прежнего API без нового механизма состояния.
    page.get_by_role('button', name='Центр', exact=True).click()
    page.get_by_text('1 мин', exact=True).click()
    movement.click()
    with page.expect_request(lambda request: request.url.endswith('/api/game/start')) as request_info:
        page.locator('#start-btn').click()
    payload = request_info.value.post_data_json
    assert payload['difficulty'] == 'center'
    assert payload['time_limit'] == 60
    assert payload['no_move'] is True
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'), timeout=10000)
    # Лицензируемый ALS SPb на production установлен отдельно и
    # намеренно не входит в Git; CI проверяет системный fallback.
    assert all(url.endswith('/static/fonts/ALS_SPb.woff2') for url in failed_resources)
    assert console_issues == []


def test_district_choice_polish_and_inactive_slider(page, server):
    """Район — явная альтернатива, а приглушённая шкала остаётся рабочей."""
    page.set_viewport_size({'width': 320, 'height': 780})
    page.goto(server)

    separator = page.locator('.district-choice-separator')
    expect(separator).to_be_visible()
    expect(separator).to_have_text('или')
    assert separator.evaluate(
        'element => element.getBoundingClientRect().height'
    ) <= 24

    icon = page.locator('.district-action-icon')
    expect(icon).to_have_text('🗺️')
    expect(icon).to_have_attribute('aria-hidden', 'true')
    assert icon.evaluate('element => element.tagName') == 'SPAN'
    emoji = icon.locator('.district-action-emoji')
    emblem = icon.locator('.district-action-emblem')
    expect(emoji).to_be_visible()
    expect(emblem).to_be_hidden()
    expect(emblem).to_have_attribute('alt', '')
    expect(emblem).to_have_attribute('aria-hidden', 'true')
    icon_alignment = page.locator('#district-picker-btn').evaluate("""
        button => {
            const icon = button.querySelector('.district-action-icon').getBoundingClientRect();
            const label = button.querySelector('#district-action-label').getBoundingClientRect();
            return Math.abs((icon.top + icon.height / 2) - (label.top + label.height / 2));
        }
    """)
    assert icon_alignment <= 2

    action_style = page.locator('#district-picker-btn').evaluate("""
        button => {
            const style = getComputedStyle(button);
            return {
                borderWidth: style.borderTopWidth,
                borderStyle: style.borderTopStyle,
                borderRadius: style.borderTopLeftRadius,
            };
        }
    """)
    assert action_style == {
        'borderWidth': '1px',
        'borderStyle': 'solid',
        'borderRadius': '12px',
    }

    alternative_spacing = page.locator('#difficulty-group').evaluate("""
        group => {
            const textRect = element => {
                const range = document.createRange();
                range.selectNodeContents(element);
                return range.getBoundingClientRect();
            };
            const labelBottom = Math.max(...[...group.querySelectorAll('.territory-label')]
                .map(label => textRect(label).bottom));
            const separatorText = textRect(
                group.querySelector('.district-choice-separator span'));
            const action = group.querySelector('#district-picker-btn').getBoundingClientRect();
            return {
                before: separatorText.top - labelBottom,
                after: action.top - separatorText.bottom,
            };
        }
    """)
    assert abs(alternative_spacing['before'] - alternative_spacing['after']) <= 2

    # И custom track, и native range остаются внутри wrapper. У wrapper нет
    # clipping, а крайние точки track имеют место для собственного stroke.
    endpoint_geometry = page.locator('.territory-slider-wrap').evaluate("""
        wrapper => {
            const wrap = wrapper.getBoundingClientRect();
            const slider = wrapper.querySelector('.territory-slider').getBoundingClientRect();
            const track = wrapper.querySelector('.territory-track').getBoundingClientRect();
            return {
                overflow: getComputedStyle(wrapper).overflow,
                sliderLeft: slider.left - wrap.left,
                sliderRight: wrap.right - slider.right,
                trackLeft: track.left - wrap.left,
                trackRight: wrap.right - track.right,
            };
        }
    """)
    assert endpoint_geometry['overflow'] == 'visible'
    assert endpoint_geometry['sliderLeft'] >= -0.01
    assert endpoint_geometry['sliderRight'] >= -0.01
    assert endpoint_geometry['trackLeft'] >= 8
    assert endpoint_geometry['trackRight'] >= 8

    def territory_styles():
        return page.locator('#territory-control').evaluate("""
            control => {
                const style = getComputedStyle(control);
                const track = control.querySelector('.territory-track');
                const label = control.querySelector('.territory-label');
                return {
                    opacity: style.opacity,
                    track: getComputedStyle(track).backgroundColor,
                    fill: style.getPropertyValue('--territory-fill-color').trim(),
                    tick: style.getPropertyValue('--territory-tick-color').trim(),
                    label: getComputedStyle(label).color,
                    thumb: style.getPropertyValue('--territory-thumb-color').trim(),
                };
            }
        """)

    active_styles = territory_styles()
    territory = page.get_by_role('slider', name='Территория')
    cases = [('Центр', '0'), ('Средняя', '1'), ('Весь город', '2')]
    for label, value in cases:
        # Сначала фиксируем стандартную позицию, затем включаем район поверх неё.
        page.get_by_role('button', name=label, exact=True).click()
        page.locator('#district-picker-btn').click()
        page.locator('#district-list').select_option('krasnogvardeysky')
        page.get_by_role('button', name='Выбрать район').click()

        expect(territory).to_be_enabled()
        expect(territory).to_have_value(value)
        expect(territory).to_have_attribute(
            'aria-valuetext', re.compile('Активен Красногвардейский район'))
        expect(page.locator('#territory-control')).to_have_class(
            re.compile('district-active'))
        expect(page.get_by_role('button', name=label, exact=True)).to_have_attribute(
            'aria-pressed', 'false')
        expect(emblem).to_be_visible()
        expect(emoji).to_be_hidden()
        expect(emblem).to_have_attribute(
            'src', re.compile(
                r'/static/img/district-icons/krasnogvardeysky\.svg\?v=\d+$'))
        assert emblem.evaluate('image => image.complete && image.naturalWidth > 0')

        inactive_styles = territory_styles()
        # Приглушаются отдельные части, а не весь native control: это сохраняет
        # hit area и не создаёт WebKit clipping layer вокруг крайнего thumb.
        assert inactive_styles['opacity'] == '1'
        assert _color_alpha(inactive_styles['track']) < _color_alpha(active_styles['track'])
        assert _color_alpha(inactive_styles['fill']) < _color_alpha(active_styles['fill'])
        assert _color_alpha(inactive_styles['tick']) < _color_alpha(active_styles['tick'])
        assert _color_alpha(inactive_styles['label']) < _color_alpha(active_styles['label'])
        assert inactive_styles['thumb'] != active_styles['thumb']

        # Любая из трёх подписей остаётся полноценным способом выйти из района.
        page.get_by_role('button', name=label, exact=True).click()
        expect(page.locator('#territory-value')).to_have_text(label)
        expect(page.locator('#territory-control')).not_to_have_class(
            re.compile('district-active'))
        expect(page.get_by_role('button', name=label, exact=True)).to_have_attribute(
            'aria-pressed', 'true')
        expect(page.locator('#district-action-label')).to_have_text(
            'Выбрать конкретный район')
        expect(emoji).to_be_visible()
        expect(emblem).to_be_hidden()
        assert emblem.get_attribute('src') is None

    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


def test_missing_district_emblem_keeps_map_emoji(page, server):
    """Ошибка декоративного SVG не оставляет пустой или broken-image слот."""
    page.route(
        '**/static/img/district-icons/krasnogvardeysky.svg*',
        lambda route: route.fulfill(status=404, content_type='image/svg+xml', body=''),
    )
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('krasnogvardeysky')
    page.get_by_role('button', name='Выбрать район').click()

    expect(page.locator('#district-action-label')).to_have_text(
        'Красногвардейский район')
    expect(page.locator('.district-action-emoji')).to_be_visible()
    expect(page.locator('.district-action-emblem')).to_be_hidden()
    assert page.locator('.district-action-emblem').get_attribute('src') is None


def test_district_map_mobile_fit_zoom_pan_and_reset(page, server):
    """Responsive fit, bounded camera и touch policy работают как единое целое."""
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(server)
    page.locator('#district-picker-btn').click()

    svg = page.locator('#district-map')
    reset = page.locator('#district-map-reset')
    expect(svg).to_be_visible(timeout=10000)
    expect(page.locator('#district-map .district-shape')).to_have_count(18)
    expect(page.locator('#district-map-zoom')).to_have_count(0)
    expect(page.get_by_role('button', name='Центр крупнее')).to_have_count(0)

    # На узком viewport измеряем именно видимый union paths, а не только размер
    # большого контейнера. Старый fixed 1000x680 fit давал около 68% x 63%.
    for width, height in [(320, 780), (375, 812), (390, 844), (430, 860)]:
        page.set_viewport_size({'width': width, 'height': height})
        page.wait_for_timeout(50)  # один кадр ResizeObserver для responsive fit
        coverage = svg.evaluate("""
            element => {
                const viewport = element.getBoundingClientRect();
                const rects = Array.from(element.querySelectorAll('.district-shape'))
                    .map(path => path.getBoundingClientRect());
                const left = Math.min(...rects.map(rect => rect.left));
                const right = Math.max(...rects.map(rect => rect.right));
                const top = Math.min(...rects.map(rect => rect.top));
                const bottom = Math.max(...rects.map(rect => rect.bottom));
                return {
                    widthShare: (right - left) / viewport.width,
                    heightShare: (bottom - top) / viewport.height,
                    contained: left >= viewport.left - 1 && right <= viewport.right + 1 &&
                        top >= viewport.top - 1 && bottom <= viewport.bottom + 1,
                };
            }
        """)
        assert coverage['widthShare'] >= 0.88
        assert coverage['heightShare'] >= 0.84
        assert coverage['contained']
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

    page.set_viewport_size({'width': 390, 'height': 844})
    page.wait_for_timeout(50)
    fit = _viewbox(page)
    expect(reset).to_be_hidden()
    assert svg.evaluate('element => getComputedStyle(element).touchAction') == 'pan-y'

    # Wheel zoom привязан к cursor anchor, а не к условному центру canvas.
    anchor = page.locator(
        '#district-map [data-district-id="petrogradsky"]'
    ).bounding_box()
    page.mouse.move(anchor['x'] + anchor['width'] / 2,
                    anchor['y'] + anchor['height'] / 2)
    page.mouse.wheel(0, -500)
    expect(svg).to_have_class(re.compile('is-zoomed'))
    expect(reset).to_be_visible()
    zoomed = _viewbox(page)
    assert zoomed[2] < fit[2]
    assert zoomed[3] < fit[3]
    _assert_viewbox_inside(zoomed, fit)
    assert svg.evaluate('element => getComputedStyle(element).touchAction') == 'none'
    assert reset.evaluate('element => element.getBoundingClientRect().height') >= 44

    # Даже заведомо чрезмерный drag не может унести карту за исходный fit.
    map_box = svg.bounding_box()
    center_x = map_box['x'] + map_box['width'] / 2
    center_y = map_box['y'] + map_box['height'] / 2
    for delta_x, delta_y in [
            (map_box['width'] * 3, 0), (-map_box['width'] * 3, 0),
            (0, map_box['height'] * 3), (0, -map_box['height'] * 3)]:
        page.mouse.move(center_x, center_y)
        page.mouse.down()
        page.mouse.move(center_x + delta_x, center_y + delta_y, steps=4)
        page.mouse.up()
        _assert_viewbox_inside(_viewbox(page), fit)
        expect(page.locator('#district-selected-name')).to_have_text('Район не выбран')

    reset.click()
    expect(svg).not_to_have_class(re.compile('is-zoomed'))
    expect(reset).to_be_hidden()
    assert _viewbox(page) == pytest.approx(fit, abs=0.05)
    assert svg.evaluate('element => getComputedStyle(element).touchAction') == 'pan-y'

    # Safari отменяет Pointer Events посреди pinch, но Touch Events продолжаются.
    outcome = svg.evaluate("""
        element => {
            const rect = element.getBoundingClientRect();
            const y = rect.top + rect.height / 2;
            const center = rect.left + rect.width / 2;
            const paths = element.querySelectorAll('path');
            const touch = (identifier, x) => new Touch({identifier,
                target: paths[identifier - 41], clientX: x, clientY: y});
            const fire = (type, touches, changedTouches = touches) => {
                const event = new TouchEvent(type, {bubbles: true, cancelable: true,
                    touches, targetTouches: touches.slice(0, 1), changedTouches});
                paths[0].dispatchEvent(event);
                return event.defaultPrevented;
            };
            const one = fire('touchstart', [touch(41, center - 30)]);
            const two = fire('touchstart', [touch(41, center - 30), touch(42, center + 30)]);
            fire('touchmove', [touch(41, center - 60), touch(42, center + 60)]);
            const before = element.viewBox.baseVal.width;
            for (const pointerId of [41, 42]) element.dispatchEvent(new PointerEvent(
                'pointercancel', {bubbles: true, pointerType: 'touch', pointerId}));
            const move = fire('touchmove', [touch(41, center - 90), touch(42, center + 90)]);
            const after = element.viewBox.baseVal.width;
            fire('touchend', [], [touch(41, center - 90), touch(42, center + 90)]);
            return {one, two, move, before, after};
        }
    """)
    assert not outcome['one']
    assert outcome['two'] and outcome['move']
    assert outcome['after'] < outcome['before']
    expect(svg).to_have_class(re.compile('is-zoomed'))
    assert _viewbox(page)[2] < fit[2]
    _assert_viewbox_inside(_viewbox(page), fit)
    reset.click()

    # Camera state не протекает через закрытие и повторное открытие picker.
    page.mouse.move(center_x, center_y)
    page.mouse.wheel(0, -400)
    expect(svg).to_have_class(re.compile('is-zoomed'))
    page.locator('#district-cancel-btn').click()
    page.locator('#district-picker-btn').click()
    expect(svg).to_be_visible()
    expect(svg).not_to_have_class(re.compile('is-zoomed'))
    expect(reset).to_be_hidden()
    assert _viewbox(page) == pytest.approx(fit, abs=0.05)


def test_district_picker_map_state_keyboard_and_standard_mode(page, server):
    """Район выбирается картой/клавиатурой и не блокирует прежнюю шкалу."""
    console_issues = []
    page.on('pageerror', lambda error: console_issues.append(str(error)))
    page.on('console', lambda message: console_issues.append(message.text)
            if message.type in ('warning', 'error')
            and not message.text.startswith('Failed to load resource:') else None)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(server)

    picker = page.get_by_role('button', name='Выбрать конкретный район')
    picker.focus()
    page.keyboard.press('Enter')
    expect(page.locator('#district-screen')).to_have_class(re.compile('active'))
    title = page.locator('#district-screen-title')
    expect(title).to_be_focused()
    assert title.evaluate('element => getComputedStyle(element).outlineStyle') == 'none'
    expect(page.locator('#district-map')).to_be_visible(timeout=10000)
    expect(page.locator('#district-map .district-shape')).to_have_count(18)
    expect(page.locator('#district-list')).to_be_visible()
    expect(page.locator('#district-selected-name')).to_have_text('Район не выбран')
    expect(page.get_by_role('button', name='Выбрать район')).to_be_disabled()
    assert page.locator('#district-map .district-shape[tabindex="0"]').count() == 1
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

    # Zoom остаётся лишь способом облегчить hit target: компактные районы всё
    # равно имеют полноценные radio semantics и синхронизируются со списком.
    compact_districts = [
        ('petrogradsky', 'Петроградский район'),
        ('tsentralny', 'Центральный район'),
        ('admiralteysky', 'Адмиралтейский район'),
        ('vasileostrovsky', 'Василеостровский район'),
    ]
    reset = page.locator('#district-map-reset')
    for district_id, district_name in compact_districts:
        if reset.is_visible():
            reset.click()
        path = page.locator(
            f'#district-map [data-district-id="{district_id}"]'
        )
        path_box = path.bounding_box()
        page.mouse.move(path_box['x'] + path_box['width'] / 2,
                        path_box['y'] + path_box['height'] / 2)
        page.mouse.wheel(0, -500)
        expect(page.locator('#district-map')).to_have_class(re.compile('is-zoomed'))
        path.click()
        expect(page.locator('#district-selected-name')).to_have_text(district_name)
        expect(path).to_have_attribute('aria-checked', 'true')
        expect(path).to_have_attribute('tabindex', '0')
        expect(page.locator('#district-list')).to_have_value(district_id)
        assert page.locator('#district-map .district-shape[aria-checked="true"]').count() == 1

    # Arrow navigation не конфликтует с camera handlers и сохраняет roving tab.
    tsentralny = page.locator('#district-map [data-district-id="tsentralny"]')
    tsentralny.focus()
    page.keyboard.press('Space')
    page.keyboard.press('ArrowRight')
    admiralteysky = page.locator(
        '#district-map [data-district-id="admiralteysky"]'
    )
    expect(admiralteysky).to_be_focused()
    expect(admiralteysky).to_have_attribute('aria-checked', 'true')
    expect(page.locator('#district-list')).to_have_value('admiralteysky')

    # Возвращаем ожидаемый район для остального state/API scenario.
    petrogradsky = page.locator(
        '#district-map [data-district-id="petrogradsky"]'
    )
    petrogradsky.focus()
    page.keyboard.press('Space')
    expect(page.locator('#district-selected-name')).to_have_text('Петроградский район')
    expect(page.locator('#district-list')).to_have_value('petrogradsky')
    page.get_by_role('button', name='Выбрать район').click()

    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#territory-value')).to_have_text('Петроградский район')
    expect(page.locator('#territory-control')).to_have_class(
        re.compile('district-active'))
    expect(page.get_by_role('slider', name='Территория')).to_be_enabled()
    emblem = page.locator('.district-action-emblem')
    expect(emblem).to_be_visible()
    expect(emblem).to_have_attribute(
        'src', re.compile(r'/petrogradsky\.svg\?v=\d+$'))

    # Внутренняя навигация приложения сохраняет ту же настройку территории.
    page.locator('#show-leaderboard-btn').click()
    expect(page.locator('#leaderboard-screen')).to_have_class(re.compile('active'))
    page.locator('#back-btn').click()
    expect(page.locator('#territory-value')).to_have_text('Петроградский район')

    # Повторное открытие показывает текущий район; карта поддерживает radio-
    # keyboard pattern, Escape отменяет draft и возвращает focus.
    page.get_by_role('button', name=re.compile('Изменить район')).click()
    expect(page.locator('#district-list')).to_have_value('petrogradsky')
    admiralteysky = page.locator(
        '#district-map [data-district-id="admiralteysky"]'
    )
    admiralteysky.focus()
    page.keyboard.press('Space')
    expect(page.locator('#district-selected-name')).to_have_text('Адмиралтейский район')
    page.keyboard.press('Escape')
    expect(page.locator('#territory-value')).to_have_text('Петроградский район')
    expect(page.locator('#district-picker-btn')).to_be_focused()
    expect(emblem).to_have_attribute(
        'src', re.compile(r'/petrogradsky\.svg\?v=\d+$'))

    # Любой обычный preset атомарно снимает район; отдельный reset не нужен.
    page.get_by_role('button', name='Центр', exact=True).click()
    expect(page.locator('#territory-value')).to_have_text('Центр')
    expect(page.locator('#territory-control')).not_to_have_class(
        re.compile('district-active'))
    expect(page.locator('#district-action-label')).to_have_text(
        'Выбрать конкретный район')
    expect(page.locator('.district-action-emoji')).to_be_visible()
    expect(emblem).to_be_hidden()

    # Native select — компактный accessibility/touch fallback. Выбранная пара
    # доходит до того же /start как единое district state.
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('kolpinsky')
    expect(page.locator('#district-selected-name')).to_have_text('Колпинский район')
    page.get_by_role('button', name='Выбрать район').click()
    expect(emblem).to_be_visible()
    expect(emblem).to_have_attribute(
        'src', re.compile(r'/kolpinsky\.svg\?v=\d+$'))
    with page.expect_request(
            lambda request: request.url.endswith('/api/game/start')) as request_info:
        page.locator('#start-btn').click()
    payload = request_info.value.post_data_json
    assert payload['difficulty'] == 'district'
    assert payload['district_id'] == 'kolpinsky'
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'), timeout=10000)
    assert console_issues == []


def test_district_picker_list_fallback_when_geometry_fails(page, server):
    """Локальная карта может сломаться отдельно, но район всё ещё выбирается."""
    page.route('**/api/districts/geometry', lambda route: route.fulfill(
        status=500, content_type='application/json', body='{"error":"broken"}'))
    page.goto(server)
    page.locator('#district-picker-btn').click()

    expect(page.locator('#district-map-error')).to_be_visible(timeout=10000)
    expect(page.locator('#district-list')).to_be_visible()
    page.locator('#district-list').select_option('kronshtadtsky')
    page.get_by_role('button', name='Выбрать район').click()
    expect(page.locator('#territory-value')).to_have_text('Кронштадтский район')
    expect(page.locator('#territory-control')).to_have_class(
        re.compile('district-active'))


def test_settings_have_styled_initial_render_without_javascript(browser, server):
    """CSS оформляет native controls до и независимо от выполнения entry JS."""
    context = browser.new_context(
        viewport={'width': 320, 'height': 780},
        java_script_enabled=False,
    )
    page = context.new_page()
    try:
        page.goto(server)
        expect(page.locator('#territory-value')).to_have_text('Средняя')
        expect(page.locator('#territory-range')).to_be_visible()
        expect(page.locator('.segmented-control')).to_be_visible()
        expect(page.locator('.switch-control')).to_be_visible()
        expect(page.locator('.district-action-emoji')).to_be_visible()
        expect(page.locator('.district-action-emblem')).to_be_hidden()
        expect(page.locator('.difficulty-btn, .timer-btn, .move-btn')).to_have_count(0)

        styles = page.locator('#territory-range').evaluate(
            "element => ({"
            "appearance: getComputedStyle(element).appearance,"
            "height: element.getBoundingClientRect().height"
            "})"
        )
        assert styles['appearance'] == 'none'
        assert styles['height'] >= 44
        assert page.locator('.territory-label').first.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == 'rgba(0, 0, 0, 0)'
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    finally:
        context.close()


def test_missing_settings_component_does_not_abort_other_controls(page, server):
    """Несовместимый/опциональный блок не обрушает инициализацию соседних."""
    page_errors = []
    page.on('pageerror', lambda error: page_errors.append(str(error)))
    page.add_init_script("""
        document.addEventListener('DOMContentLoaded', () => {
            document.getElementById('difficulty-group')?.remove();
        }, { once: true });
    """)
    page.goto(server)

    page.get_by_text('1 мин', exact=True).click()
    expect(page.get_by_role('radio', name='1 мин')).to_be_checked()
    movement = page.get_by_role('switch')
    movement.click()
    expect(movement).not_to_be_checked()
    expect(page.locator('#movement-title')).to_have_text('Нельзя перемещаться')
    assert page_errors == []


def test_full_game_flow(page, server):
    # Первый location приходит прямо из /start, последующие — из ответа guess.
    # Отдельные GET больше не нужны; их появление означает потерю prefetch-задачи.
    location_requests = []
    page.on('request', lambda req: location_requests.append(req.url)
            if '/api/game/location' in req.url and 'peek' not in req.url else None)

    page.goto(server)
    expect(page).to_have_title(re.compile('Петербургский следопыт'))

    # Стартовый экран: все контролы на месте
    expect(page.locator('#start-btn')).to_be_visible()
    expect(page.locator('#daily-btn')).to_be_visible()
    expect(page.locator('#move-group')).to_be_visible()

    page.locator('#player-name').fill('E2E-игрок')
    page.locator('#start-btn').click()

    expect(page.locator('#game-screen')).to_have_class(re.compile('active'), timeout=10000)

    for round_no in range(1, 6):
        expect(page.locator('#current-round')).to_have_text(str(round_no), timeout=10000)
        _play_round(page)

        # Адрес из клиентского геокодера-заглушки
        expect(page.locator('#correct-location-name')).to_have_text('Тестовая улица, 1', timeout=5000)

        if round_no < 5:
            page.locator('#next-round-btn').click()
            expect(page.locator('#game-screen')).to_have_class(re.compile('active'))

    # Финал
    page.locator('#next-round-btn').click()
    expect(page.locator('#final-screen')).to_have_class(re.compile('active'), timeout=10000)
    expect(page.locator('#final-score')).not_to_have_text('0')
    expect(page.locator('#rounds-summary .round-item')).to_have_count(5)
    expect(page.locator('#challenge-btn')).to_be_visible()
    assert page.evaluate("""() => ({
        canvas: getComputedStyle(document.documentElement).backgroundColor,
        theme: document.querySelector('meta[name="theme-color"]').content,
    })""") == {'canvas': 'rgb(246, 243, 228)', 'theme': '#f6f3e4'}
    page.wait_for_function('window.__ymapsStats.mapsActive === 1')
    resource_stats = page.evaluate('window.__ymapsStats')
    assert resource_stats['panoramaPlayersActive'] == 0

    # Таблица лидеров: игрок попал в топ
    page.locator('#final-leaderboard-btn').click()
    expect(page.locator('#leaderboard-screen')).to_have_class(re.compile('active'))
    expect(page.locator('.leaderboard-table')).to_contain_text('E2E-игрок')
    assert page.evaluate('window.__ymapsStats.mapsActive') == 0

    assert len(location_requests) == 0, \
        f'ожидалось 0 дополнительных запросов раунда, было {len(location_requests)}'


def test_no_move_round_and_map_toggle(page, server):
    # Мобильная ширина: «ручка» панели карты существует только там
    page.set_viewport_size({'width': 420, 'height': 820})
    page.goto(server)

    # Включаем «не сходя с места» и играем один раунд
    page.get_by_role('switch', name=re.compile('Можно перемещаться')).click()
    page.locator('#start-btn').click()
    expect(page.locator('#game-screen')).to_have_class(re.compile('active'), timeout=10000)
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)

    # На мобильных раунд начинается со свёрнутой картой: первым делом игрок
    # осматривается, и панорама сразу на весь экран
    expect(page.locator('#map-panel')).to_have_class(re.compile('collapsed'))

    # Ручка «нижнего листа» разворачивает и сворачивает панель
    page.locator('#map-handle').click()
    expect(page.locator('#map-panel')).not_to_have_class(re.compile('collapsed'))
    page.locator('#map-handle').click()
    expect(page.locator('#map-panel')).to_have_class(re.compile('collapsed'))
    page.locator('#map-handle').click()

    _play_round(page)
    expect(page.locator('#result-score')).to_be_visible()

    # Адрес на результате — ссылка на панораму места в Яндекс Картах
    expect(page.locator('#correct-location-name')).to_have_attribute(
        'href', re.compile(r'yandex\.ru/maps/\?panorama'))


def test_mobile_result_map_meets_panel_at_dynamic_viewport_heights(page, server):
    """За скруглением видна карта; обычный экран показывает кнопку без прокрутки."""
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(server)
    page.locator('#start-btn').click()
    page.locator('#map-handle').click()
    _play_round(page)

    for height in (700, 844, 900, 560):
        page.set_viewport_size({'width': 390, 'height': height})
        layout = page.evaluate("""() => {
            const map = document.querySelector('.result-map-container').getBoundingClientRect();
            const panelElement = document.querySelector('.result-panel');
            panelElement.scrollTop = 0;
            const panel = panelElement.getBoundingClientRect();
            const button = document.querySelector('#next-round-btn').getBoundingClientRect();
            const style = element => getComputedStyle(element).backgroundColor;
            return {
                gap: panel.top - map.bottom,
                visibleMapHeight: panel.top - map.top,
                stageColor: style(document.querySelector('.result-stage')),
                panelColor: style(panelElement),
                radius: getComputedStyle(panelElement).borderTopLeftRadius,
                mapBehindCorner: !!document.elementFromPoint(1, panel.top + 1)?.closest('#result-map'),
                panelClientHeight: panelElement.clientHeight,
                panelScrollHeight: panelElement.scrollHeight,
                buttonBottom: button.bottom,
                panelBottom: panel.bottom,
                htmlColor: style(document.documentElement),
                bodyColor: style(document.body),
                overflowX: document.documentElement.scrollWidth > innerWidth,
            };
        }""")
        assert abs(layout['gap'] + 8) <= 1, layout
        assert layout['visibleMapHeight'] >= 159, layout
        assert layout['radius'] == '8px', layout
        assert layout['mapBehindCorner'], layout
        assert layout['stageColor'] == layout['panelColor'] == layout['htmlColor'] == layout['bodyColor'] == 'rgb(35, 28, 98)'
        assert not layout['overflowX'], layout
        if height >= 700:
            assert layout['panelScrollHeight'] <= layout['panelClientHeight'] + 1, layout
            assert layout['buttonBottom'] <= layout['panelBottom'] + 1, layout
        page.locator('#next-round-btn').scroll_into_view_if_needed()
        expect(page.locator('#next-round-btn')).to_be_in_viewport()


def test_document_canvas_follows_screen_theme(page, server):
    """Overscroll и тема браузера используют фон текущего экрана."""
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(server)

    def colors():
        return page.evaluate("""() => ({
            html: getComputedStyle(document.documentElement).backgroundColor,
            body: getComputedStyle(document.body).backgroundColor,
            theme: document.querySelector('meta[name="theme-color"]').content,
            overflowX: document.documentElement.scrollWidth > innerWidth,
        })""")

    assert colors() == {
        'html': 'rgb(35, 28, 98)', 'body': 'rgb(35, 28, 98)',
        'theme': '#231c62', 'overflowX': False,
    }
    page.locator('#show-leaderboard-btn').click()
    expect(page.locator('#leaderboard-screen')).to_have_class(re.compile('active'))
    assert colors() == {
        'html': 'rgb(246, 243, 228)', 'body': 'rgb(246, 243, 228)',
        'theme': '#f6f3e4', 'overflowX': False,
    }
    page.locator('#back-btn').click()
    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))
    assert colors() == {
        'html': 'rgb(35, 28, 98)', 'body': 'rgb(35, 28, 98)',
        'theme': '#231c62', 'overflowX': False,
    }


def test_fast_continue_reuses_inflight_prefetch(page, server):
    """Быстрый переход не запускает второй locate и скрытый Player."""
    page.add_init_script('window.__ymapsLocateDelay = 350')
    page.goto(server)
    page.locator('#start-btn').click()
    _play_round(page)

    # Нажимаем сразу после появления результата, пока locate следующего раунда
    # с искусственной задержкой почти наверняка ещё выполняется.
    page.locator('#next-round-btn').click()
    expect(page.locator('#current-round')).to_have_text('2', timeout=10000)
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)

    stats = page.evaluate('window.__ymapsStats')
    assert stats['locateCalls'] == 2  # первый раунд + один общий prefetch второго
    assert stats['panoramaPlayersActive'] == 1
    assert stats['panoramaPlayersCreated'] == 2


def test_missing_new_place_falls_back_without_reload(page, server):
    """Пустой locate исследовательской точки автоматически восстанавливает раунд."""
    page.add_init_script('window.__ymapsEmptyLocateCount = 1')
    page.goto(server)
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    stats = page.evaluate('window.__ymapsStats')
    assert stats['locateCalls'] == 2
    assert stats['panoramaPlayersActive'] == 1


def test_district_search_continues_after_four_empty_locations(page, server):
    """Несколько пустых locate подряд не прерывают поиск доступной съёмки."""
    page.add_init_script('window.__ymapsEmptyLocateCount = 4')
    skip_requests = []
    page.on('request', lambda request: skip_requests.append(request)
            if request.url.endswith('/api/game/skip_location') else None)
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('kurortny')
    page.get_by_role('button', name='Выбрать район').click()
    page.evaluate("""() => {
        const overlay = document.getElementById('photo-overlay');
        const button = document.getElementById('continue-search-btn');
        window.__searchPaused = false;
        new MutationObserver(() => {
            if (!overlay.classList.contains('hidden') &&
                    !button.classList.contains('hidden')) {
                window.__searchPaused = true;
            }
        }).observe(overlay, {attributes: true, subtree: true, attributeFilter: ['class']});
    }""")
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    assert not page.evaluate('window.__searchPaused')
    assert len(skip_requests) == 4
    stats = page.evaluate('window.__ymapsStats')
    assert stats['locateCalls'] == 5
    assert stats['panoramaPlayersCreated'] == 1
    assert stats['panoramaPlayersActive'] == 1


def test_district_without_coverage_can_return_to_preserved_settings(page, server):
    """После полного поиска игрок может вернуться к прежним настройкам."""
    page.set_viewport_size({'width': 390, 'height': 844})
    page.add_init_script('window.__ymapsEmptyLocateCount = 100')
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('kurortny')
    page.get_by_role('button', name='Выбрать район').click()
    page.locator('#start-btn').click()

    expect(page.locator('#photo-overlay')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay > span')).to_have_text(
        'Панорама пока не найдена. Можно продолжить поиск в этом раунде — набранные очки сохранятся.'
    )
    expect(page.get_by_role('button', name='Повторить')).to_be_hidden()
    expect(page.get_by_role('button', name='Продолжить поиск')).to_be_visible()
    back = page.get_by_role('button', name='К настройкам')
    expect(back).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

    back.click()
    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#territory-value')).to_have_text('Курортный район')
    expect(page.locator('#territory-control')).to_have_class(
        re.compile('district-active'))
    expect(page.locator('#district-picker-btn')).to_be_focused()
    page.wait_for_function(
        'window.__ymapsStats.panoramaPlayersActive === 0 && '
        'window.__ymapsStats.mapsActive === 0'
    )


def test_district_skip_limit_has_honest_recovery_message(page, server):
    """429 лимита замен не маскируется под сетевую ошибку и имеет выход."""
    page.add_init_script('window.__ymapsEmptyLocateCount = 100')
    page.route('**/api/game/skip_location', lambda route: route.fulfill(
        status=429,
        content_type='application/json',
        body='{"error":"Серия поиска завершена","reason":"search_batch_exhausted"}',
    ))
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('kronshtadtsky')
    page.get_by_role('button', name='Выбрать район').click()
    page.locator('#start-btn').click()

    expect(page.locator('#photo-overlay > span')).to_have_text(
        'Панорама пока не найдена. Можно продолжить поиск в этом раунде — набранные очки сохранятся.',
        timeout=10000,
    )
    expect(page.get_by_role('button', name='Повторить')).to_be_hidden()
    expect(page.get_by_role('button', name='Продолжить поиск')).to_be_visible()
    expect(page.get_by_role('button', name='К настройкам')).to_be_visible()


def test_standard_skip_limit_still_has_settings_action(page, server):
    """После предела поиска обычная игра тоже не остаётся без действий."""
    page.add_init_script('window.__ymapsEmptyLocateCount = 100')
    page.route('**/api/game/skip_location', lambda route: route.fulfill(
        status=429,
        content_type='application/json',
        body='{"error":"Серия поиска завершена","reason":"search_batch_exhausted"}',
    ))
    page.goto(server)
    page.locator('#start-btn').click()

    back = page.get_by_role('button', name='К настройкам')
    expect(back).to_be_visible(timeout=10000)
    back.click()
    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))


def test_continue_search_preserves_round_after_lost_response_and_double_click(page, server):
    """Две конечные серии, потерянный ответ и повтор кнопки сохраняют игру."""
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("""
        const originalFetch = window.fetch.bind(window);
        window.__continueRequests = 0;
        window.__loseSearchResponses = 2;
        window.fetch = async (...args) => {
            const response = await originalFetch(...args);
            if (String(args[0]).includes('/api/game/continue_search')) {
                window.__continueRequests++;
                if (window.__loseSearchResponses-- > 0) {
                    throw new TypeError('test: lost continuation response');
                }
            }
            return response;
        };
    """)
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('kurortny')
    page.locator('#district-confirm-btn').click()
    page.locator('label[for="timer-60"]').click()
    page.locator('#start-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    page.evaluate('window.__ymapsEmptyLocateCount = 100')
    _play_round(page)
    score = page.locator('#total-score').inner_text()
    page.locator('#next-round-btn').click()
    button = page.get_by_role('button', name='Продолжить поиск')
    expect(button).to_be_visible(timeout=15000)
    get_location = "async () => (await import('/static/js/state.js')).state.currentLocation"
    original = page.evaluate(get_location)
    assert original['location_version'] == 10
    assert original['round'] == 2
    expect(page.locator('#guess-btn')).to_be_disabled()
    assert page.evaluate('window.__ymapsStats.panoramaPlayersCreated') == 1

    # Оба ответа разрешения потерялись. На сервере открыта только вторая серия.
    button.click()
    expect(page.locator('#photo-overlay > span')).to_have_text('Нет соединения с сервером')
    assert page.evaluate('window.__continueRequests') == 2
    assert page.evaluate(get_location)['search_batch'] == 1
    page.evaluate('''() => {
        window.__ymapsEmptyLocateCount = 11;
        const button = document.getElementById('continue-search-btn');
        button.click(); button.click();
    }''')
    expect(button).to_be_visible(timeout=15000)
    second = page.evaluate(get_location)
    assert second['search_batch'] == 2
    assert second['location_version'] == 20
    assert page.evaluate('window.__continueRequests') == 3
    assert page.evaluate('window.__ymapsStats.panoramaPlayersCreated') == 1
    assert page.evaluate("async () => (await import('/static/js/state.js')).state.roundTimerInterval") is None
    expect(page.locator('#total-score')).to_have_text(score)

    # Следующая серия находит панораму после одной замены.
    page.evaluate('window.__ymapsEmptyLocateCount = 1')
    button.click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=15000)
    expect(page.locator('#photo-overlay')).to_be_hidden()
    location = page.evaluate(get_location)
    assert location['round_id'] == original['round_id']
    assert location['district_id'] == 'kurortny'
    assert location['search_batch'] == 3
    assert location['location_version'] == 21
    assert page.evaluate('window.__ymapsStats.panoramaPlayersCreated') == 2
    assert page.evaluate('window.__ymapsStats.panoramaPlayersActive') == 1
    expect(page.locator('#total-score')).to_have_text(score)
    expect(page.locator('#timer-chip')).to_be_visible()
    _play_round(page)
    assert not errors


def test_late_search_permission_does_not_reopen_game_after_leaving(page, server):
    page.add_init_script("""
        window.__ymapsEmptyLocateCount = 100;
        const originalFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            const response = await originalFetch(...args);
            if (String(args[0]).includes('/api/game/continue_search')) {
                await new Promise(resolve => { window.__releaseSearch = resolve; });
            }
            return response;
        };
    """)
    page.goto(server)
    page.locator('#start-btn').click()
    button = page.get_by_role('button', name='Продолжить поиск')
    expect(button).to_be_visible(timeout=15000)
    button.click()
    page.wait_for_function('typeof window.__releaseSearch === "function"')
    # Навигация пока ответ в пути: обработчик не должен оживить старый экран.
    page.locator('#back-to-district-btn').evaluate('button => button.click()')
    page.evaluate('window.__releaseSearch()')
    expect(page.locator('#start-screen')).to_have_class(re.compile('active'))
    page.wait_for_timeout(300)
    state = page.evaluate("""async () => {
        const { state } = await import('/static/js/state.js');
        return {location: state.currentLocation, loading: state.roundLoading};
    }""")
    assert state == {'location': None, 'loading': False}
    assert page.evaluate('window.__ymapsStats.panoramaPlayersCreated') == 0


def test_skip_rate_limit_keeps_retry_without_granting_a_search_batch(page, server):
    page.add_init_script('window.__ymapsEmptyLocateCount = 1')
    page.route('**/api/game/skip_location', lambda route: route.fulfill(status=429))
    page.goto(server)
    page.locator('#start-btn').click()
    expect(page.locator('#photo-overlay > span')).to_have_text(
        'Поиск временно ограничен. Подождите минуту и повторите загрузку.', timeout=10000)
    expect(page.get_by_role('button', name='Продолжить поиск')).to_be_hidden()
    expect(page.get_by_role('button', name='К настройкам')).to_be_visible()
    page.get_by_role('button', name='Повторить загрузку').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)


def test_too_distant_nearest_panorama_is_not_used_as_the_answer(page, server):
    """Ближайшая, но далёкая съёмка не рассинхронизирует картинку и счёт."""
    page.add_init_script('window.__ymapsFarLocateCount = 1')
    page.goto(server)
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    stats = page.evaluate('window.__ymapsStats')
    assert stats['locateCalls'] == 2
    assert stats['panoramaPlayersActive'] == 1


def test_panorama_outside_district_is_replaced_before_player(page, server):
    """Съёмка за границей района заменяется и не показывается игроку."""
    validation_count = 0
    skip_payloads = []

    def validate_route(route):
        nonlocal validation_count
        validation_count += 1
        if validation_count == 1:
            route.fulfill(
                status=200,
                content_type='application/json',
                body='{"valid":false,"reason":"outside_district"}',
            )
        else:
            route.continue_()

    page.route('**/api/game/validate_panorama', validate_route)
    page.on('request', lambda request: skip_payloads.append(request.post_data_json)
            if request.url.endswith('/api/game/skip_location') else None)
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('petrogradsky')
    page.get_by_role('button', name='Выбрать район').click()
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    stats = page.evaluate('window.__ymapsStats')
    assert validation_count == 2
    assert stats['locateCalls'] == 2
    assert skip_payloads[0]['reason'] == 'outside_district'
    assert stats['panoramaPlayersCreated'] == 1


def test_duplicate_panorama_in_next_round_is_replaced_before_player(page, server):
    """Повтор уже сыгранной панорамы не показывается во втором раунде."""
    round_ids = []
    rejected_duplicate = False
    skip_payloads = []

    def validate_route(route):
        nonlocal rejected_duplicate
        round_id = route.request.post_data_json['round_id']
        if round_id not in round_ids:
            round_ids.append(round_id)
        if len(round_ids) == 2 and not rejected_duplicate:
            rejected_duplicate = True
            route.fulfill(
                status=200,
                content_type='application/json',
                body='{"valid":false,"reason":"duplicate_panorama"}',
            )
        else:
            route.continue_()

    page.route('**/api/game/validate_panorama', validate_route)
    page.on('request', lambda request: skip_payloads.append(request.post_data_json)
            if request.url.endswith('/api/game/skip_location') else None)
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('petrogradsky')
    page.get_by_role('button', name='Выбрать район').click()
    page.locator('#start-btn').click()

    _play_round(page)
    page.locator('#next-round-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)

    assert rejected_duplicate
    assert len(round_ids) == 2
    assert [payload['reason'] for payload in skip_payloads] == ['duplicate_panorama']
    assert page.evaluate('window.__ymapsStats.locateCalls') == 3
    # Игроку создали Player только для первого и заменённого второго раундов.
    assert page.evaluate('window.__ymapsStats.panoramaPlayersCreated') == 2
    location = page.evaluate("""async () => {
        const { state } = await import('/static/js/state.js');
        return state.currentLocation;
    }""")
    assert location['round'] == 2
    assert location['location_version'] == 1


def test_district_keeps_searching_after_five_duplicate_panoramas(page, server):
    """Несколько повторов подряд не запирают игрока перед вторым раундом."""
    first_round_id = None
    duplicate_count = 0
    skip_reasons = []

    def validate_route(route):
        nonlocal first_round_id, duplicate_count
        round_id = route.request.post_data_json['round_id']
        if first_round_id is None:
            first_round_id = round_id
        if round_id != first_round_id and duplicate_count < 5:
            duplicate_count += 1
            route.fulfill(
                status=200,
                content_type='application/json',
                body='{"valid":false,"reason":"duplicate_panorama"}',
            )
        else:
            route.continue_()

    page.route('**/api/game/validate_panorama', validate_route)
    page.on('request', lambda request: skip_reasons.append(
        request.post_data_json['reason'])
        if request.url.endswith('/api/game/skip_location') else None)
    page.goto(server)
    page.locator('#district-picker-btn').click()
    page.locator('#district-list').select_option('petrogradsky')
    page.get_by_role('button', name='Выбрать район').click()
    page.locator('#start-btn').click()

    _play_round(page)
    page.locator('#next-round-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)

    assert duplicate_count == 5
    assert skip_reasons == ['duplicate_panorama'] * 5
    assert page.evaluate('window.__ymapsStats.locateCalls') == 7
    assert page.evaluate('window.__ymapsStats.panoramaPlayersCreated') == 2


def test_standard_mode_replaces_repeat_before_second_player(page, server):
    """Обычный режим тоже сверяет съёмку с уже сыгранным раундом."""
    validation_count = 0
    skip_reasons = []

    def validate_route(route):
        nonlocal validation_count
        validation_count += 1
        if validation_count == 1:
            route.fulfill(
                status=200,
                content_type='application/json',
                body='{"valid":false,"reason":"duplicate_panorama"}',
            )
        else:
            route.continue_()

    page.route('**/api/game/validate_panorama', validate_route)
    page.on('request', lambda request: skip_reasons.append(
        request.post_data_json['reason'])
        if request.url.endswith('/api/game/skip_location') else None)
    page.goto(server)
    page.locator('#start-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    assert validation_count == 0  # в первом раунде сравнивать ещё не с чем

    _play_round(page)
    page.locator('#next-round-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)

    assert validation_count == 2
    assert skip_reasons == ['duplicate_panorama']
    assert page.evaluate('window.__ymapsStats.locateCalls') == 3
    assert page.evaluate('window.__ymapsStats.panoramaPlayersCreated') == 2


def test_panorama_outside_city_is_replaced_before_player(page, server):
    """«Весь город» валидирует точную границу до создания Player."""
    validation_count = 0
    skip_payloads = []

    def validate_route(route):
        nonlocal validation_count
        validation_count += 1
        valid = validation_count > 1
        route.fulfill(
            status=200,
            content_type='application/json',
            body=('{"valid":true}' if valid else
                  '{"valid":false,"reason":"outside_city"}'),
        )

    page.route('**/api/game/validate_panorama', validate_route)
    page.on('request', lambda request: skip_payloads.append(request.post_data_json)
            if request.url.endswith('/api/game/skip_location') else None)
    page.goto(server)
    page.get_by_role('button', name='Весь город', exact=True).click()
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    stats = page.evaluate('window.__ymapsStats')
    assert validation_count == 2
    assert stats['locateCalls'] == 2
    assert skip_payloads[0]['reason'] == 'outside_city'
    assert stats['panoramaPlayersCreated'] == 1


def test_transient_panorama_error_retries_same_place_without_skip(page, server):
    """Транспортный сбой не меняет место и не помечает точку пула плохой."""
    page.add_init_script('window.__ymapsNetworkFailures = 1')
    skip_requests = []
    page.on('request', lambda req: skip_requests.append(req.url)
            if '/api/game/skip_location' in req.url else None)
    page.goto(server)
    page.locator('#start-btn').click()

    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    stats = page.evaluate('window.__ymapsStats')
    assert stats['locateCalls'] == 2
    assert skip_requests == []


def test_quick_modal_close_destroys_late_panorama_player(page, server):
    """Поздний Promise полноэкранного Player не оставляет WebGL-контекст."""
    page.goto(server)
    page.locator('#start-btn').click()
    _play_round(page)

    page.evaluate('window.__ymapsPlayerDelay = 300')
    page.locator('#result-pano').click()
    page.locator('#pano-modal-close').click()
    page.wait_for_timeout(500)

    stats = page.evaluate('window.__ymapsStats')
    assert stats['panoramaPlayersActive'] == 0


def test_slow_ready_response_does_not_block_fast_next_round(page, server):
    """Сетевое подтверждение таймера не держит уже видимый интерфейс."""
    page.add_init_script("""
        const originalFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            const response = await originalFetch(...args);
            const url = String(args[0]);
            if (url.includes('/api/game/ready')) {
                await new Promise(resolve => setTimeout(resolve, 1500));
            }
            return response;
        };
    """)
    page.goto(server)
    page.locator('#start-btn').click()
    _play_round(page)

    page.locator('#next-round-btn').click()
    expect(page.locator('#current-round')).to_have_text('2', timeout=10000)
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
