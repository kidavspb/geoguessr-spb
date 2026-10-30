"""Хэллоуинская «Ночь Медного всадника»: пасхалка, режим и погоня в раунде."""
import re
from pathlib import Path

from playwright.sync_api import expect

from test_settings import _author_challenge_token

FIRST_WHISPER = 'Медный всадник сошёл с Гром-камня…'
NO_HSCROLL = 'document.documentElement.scrollWidth <= innerWidth'
NIGHT_BLUE = 'rgb(12, 41, 76)'
# Логотип — тоже переключатель. Центр кнопки всадника лежит под логотипом,
# поэтому саму кнопку тесты нажимают с клавиатуры или событием click.
LOGO = '.start-logo-wrap .start-logo:not(.chase-only)'
NIGHT_LOGO = '.start-logo-wrap .start-logo.chase-only'


def _shorten_rounds(page, remaining_ms):
    """Сервер «отвечает», что от раунда осталось remaining_ms."""
    def ready(route):
        response = route.fetch()
        data = response.json()
        data['remaining_ms'] = remaining_ms
        route.fulfill(response=response, json=data)

    page.route('**/api/game/ready', ready)


def _record_guesses(page):
    guesses = []
    page.on('request', lambda request: guesses.append(request.post_data_json)
            if request.url.endswith('/api/game/guess') else None)
    return guesses


def _open_chase(page, server):
    """Без анимаций: всадник стоит на месте, режим включается сразу."""
    page.emulate_media(reduced_motion='reduce')
    page.goto(server)
    page.locator(LOGO).click()
    expect(page.locator('#chase-title')).to_be_visible()


def _start_chase_round(page):
    page.locator('#start-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)


def test_horseman_turns_start_screen_into_chase_mode(page, halloween_server):
    console_issues = []
    page.on('pageerror', lambda error: console_issues.append(str(error)))
    page.on('console', lambda message: console_issues.append(message.text)
            if message.type == 'error'
            and not message.text.startswith('Failed to load resource:') else None)
    page.emulate_media(reduced_motion='reduce')
    page.goto(halloween_server)

    horseman = page.get_by_role('button', name=re.compile('Медный всадник'))
    expect(horseman).to_be_visible()
    expect(page.locator('#chase-title')).to_be_hidden()
    # Ждём, а не проверяем мгновенно: правило reduced motion даёт всем
    # элементам переход 0,01 мс, и сразу после смены ширины реплика всадника
    # кадр держит прежнюю ширину. Настоящее переполнение не пройдёт и так.
    for width in [320, 390, 1280]:
        page.set_viewport_size({'width': width, 'height': 800})
        page.wait_for_function(NO_HSCROLL, timeout=3000)

    # Кнопка всадника доступна с клавиатуры.
    horseman.focus()
    page.keyboard.press('Enter')
    expect(page.locator('#chase-title')).to_have_text('Ночь Медного всадника')
    expect(page.locator('.brand-title:not(.chase-only)')).to_be_hidden()
    # Под легендой — короткие правила: зачем кнопка «Бежать».
    expect(page.locator('.chase-rules')).to_contain_text('Пять раундов на время')
    expect(page.locator('html')).to_have_class(re.compile('theme-chase'))
    expect(page.locator('#start-btn')).to_have_text('Бежать')
    expect(horseman).to_have_attribute('aria-label', re.compile('Вернуться к обычной игре'))
    # Без таймера погоня невозможна: вызов дня и «Без лимита» видны, но неактивны.
    expect(page.locator('#daily-btn')).to_be_visible()
    expect(page.locator('#daily-btn')).to_be_disabled()
    expect(page.locator('label[for="timer-unlimited"]')).to_be_visible()
    expect(page.locator('#timer-unlimited')).to_be_disabled()
    expect(page.locator('input[name="round-time"]:checked')).to_have_value('60')
    # Лишних кнопок нет: выход — повторный клик по всаднику.
    expect(page.get_by_role('button', name='Назад')).to_have_count(0)
    for width in [320, 390, 1280]:
        page.set_viewport_size({'width': width, 'height': 800})
        page.wait_for_function(NO_HSCROLL, timeout=3000)

    # Цвет фона режима и в «вырезах» шкалы, и на экране выбора района.
    expect(page.locator('.territory-tick').first).to_have_css(
        'background-color', NIGHT_BLUE)
    # Все настройки главного экрана работают и в погоне, включая район.
    page.locator('#district-picker-btn').click()
    expect(page.locator('#district-screen')).to_have_css('background-color', NIGHT_BLUE)
    page.locator('#district-list').select_option('petrogradsky')
    page.locator('#district-confirm-btn').click()
    page.get_by_text('3 мин', exact=True).click()
    page.get_by_role('switch').click()
    with page.expect_request(lambda request: request.url.endswith('/api/game/start')) as start:
        _start_chase_round(page)
    payload = start.value.post_data_json
    assert payload['difficulty'] == 'district'
    assert payload['district_id'] == 'petrogradsky'
    assert payload['time_limit'] == 180
    assert payload['no_move'] is True
    assert payload['challenge_token'] is None

    expect(page.locator('.game-stage')).to_have_class(re.compile('chase-mode'))
    expect(page.locator('#chase-layer')).to_be_visible()
    expect(page.locator('#chase-whisper')).to_have_text(FIRST_WHISPER)
    expect(page.locator('#timer-chip svg.timer-icon')).to_be_visible()
    expect(page.locator('#timer-chip span.timer-icon')).to_be_hidden()
    # Чёрно-белый только снимок: логотип и копирайты Яндекса не трогаем.
    assert page.locator('#panorama-player canvas').evaluate(
        'element => getComputedStyle(element).filter') == 'grayscale(1)'
    assert page.locator('#panorama-player').evaluate(
        'element => getComputedStyle(element).filter') == 'none'
    # Погоня — только CSS: ни второго Player, ни перехвата кликов по карте.
    assert page.evaluate('window.__ymapsStats.panoramaPlayersActive') == 1
    assert page.locator('#chase-layer').evaluate(
        'element => getComputedStyle(element).pointerEvents') == 'none'

    page.locator('#map').click(position={'x': 150, 'y': 120})
    page.locator('#guess-btn').click()
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#result-title')).not_to_have_text('Всадник догнал!')
    expect(page.locator('#chase-caught')).to_be_hidden()
    assert console_issues == []


def test_horseman_sits_on_the_logo_edge_at_any_logo_size(page, halloween_server):
    """На телефоне логотип 108px вместо 132px — всадник масштабируется с ним."""
    page.emulate_media(reduced_motion='reduce')
    page.goto(halloween_server)
    logo_selector = '.start-logo-wrap .start-logo:not(.chase-only)'
    for width, logo_width in [(390, 108), (1280, 132)]:
        page.set_viewport_size({'width': width, 'height': 800})
        # Новый вьюпорт применяется не мгновенно — ждём нужный размер логотипа.
        page.wait_for_function(
            '([selector, width]) => document.querySelector(selector)'
            '.getBoundingClientRect().width === width',
            arg=[logo_selector, logo_width])
        left, top, size = page.evaluate("""selector => {
            const logo = document.querySelector(selector).getBoundingClientRect().width;
            const peek = document.getElementById('horseman-peek');
            return [peek.offsetLeft / logo, peek.offsetTop / logo, peek.offsetWidth / logo];
        }""", logo_selector)
        # Доли из примера владельца: кадр у правого верхнего края суши.
        assert abs(left - 0.987) < 0.015
        assert abs(top + 0.273) < 0.015
        assert abs(size - 0.699) < 0.015


def test_horseman_is_easy_to_hit_on_a_phone(page, halloween_server):
    """Тач-зона шире картинки: промах на палец рядом со всадником — всё равно он."""
    page.set_viewport_size({'width': 390, 'height': 844})
    page.emulate_media(reduced_motion='reduce')
    page.goto(halloween_server)
    hits = page.evaluate("""() => {
        const peek = document.getElementById('horseman-peek');
        const wrap = peek.parentElement.getBoundingClientRect();
        const angle = 55.37 * Math.PI / 180;
        // Середина внешнего края повёрнутого кадра и направление наружу.
        const x = wrap.left + peek.offsetLeft + Math.cos(angle) * peek.offsetWidth / 2;
        const y = wrap.top + peek.offsetTop + Math.sin(angle) * peek.offsetWidth / 2;
        const hit = distance => !!document.elementFromPoint(
            x + Math.sin(angle) * distance, y - Math.cos(angle) * distance,
        )?.closest('#horseman-peek');
        return [hit(4), hit(16), hit(40)];
    }""")
    assert hits == [True, True, False]


def test_logo_switches_mode_both_ways(page, halloween_server):
    """Логотип — тоже переключатель, и клик не срабатывает дважды."""
    page.emulate_media(reduced_motion='reduce')
    page.goto(halloween_server)
    page.locator(LOGO).click()
    expect(page.locator('#chase-title')).to_be_visible()
    page.locator(NIGHT_LOGO).click()
    expect(page.locator('#chase-title')).to_be_hidden()
    expect(page.locator('#start-btn')).to_have_text('Начать игру')
    # Клик по самому всаднику всплывает к тому же обработчику — режим
    # переключается один раз, а не туда и обратно.
    page.locator('#horseman-peek').dispatch_event('click')
    expect(page.locator('#chase-title')).to_be_visible()


def test_logo_glitches_in_step_with_horseman(page, halloween_server):
    """«Ночной» логотип проявляется вместе со всадником, а тапы идут в обычный."""
    page.goto(halloween_server)
    night = page.locator(NIGHT_LOGO)
    assert night.evaluate('el => getComputedStyle(el).animationName') == 'logo-glitch'
    assert night.evaluate('el => getComputedStyle(el).pointerEvents') == 'none'
    # Под пальцем — всегда видимый обычный логотип, а не прозрачная накладка.
    assert page.evaluate("""selector => {
        const box = document.querySelector(selector).getBoundingClientRect();
        return document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)
            .matches(selector);
    }""", LOGO)

    def opacities_at(ms):
        return page.evaluate("""([ms, night]) => {
            document.getAnimations().forEach(a => { a.pause(); a.currentTime = ms; });
            return [getComputedStyle(document.getElementById('horseman-peek')).opacity,
                    getComputedStyle(document.querySelector(night)).opacity];
        }""", [ms, NIGHT_LOGO])

    assert opacities_at(300) == ['0', '0']
    assert opacities_at(3000) == ['1', '1']


def test_hint_speaks_while_horseman_peeks_and_opens_the_mode(page, halloween_server):
    """На демо всадника не находили — рядом с ним реплика, и по ней можно нажать."""
    page.goto(halloween_server)
    hint = page.locator('#horseman-hint')
    expect(hint).to_have_text(re.compile(r'Убежишь\?\s*Нажми на\sменя'))

    def hint_at(ms):
        return page.evaluate("""ms => {
            document.getAnimations().forEach(a => { a.pause(); a.currentTime = ms; });
            const style = getComputedStyle(document.getElementById('horseman-hint'));
            return [style.opacity, style.visibility];
        }""", ms)

    # Пока всадник не выглянул, невидимая реплика и клики не ловит.
    assert hint_at(300) == ['0', 'hidden']
    assert hint_at(3000) == ['1', 'visible']
    hint.click()
    expect(page.locator('#chase-title')).to_be_visible(timeout=3000)
    expect(hint).to_be_hidden()


def test_hint_is_gone_once_the_easter_egg_was_found(page, halloween_server):
    _open_chase(page, halloween_server)
    page.reload()
    expect(page.locator('#chase-title')).to_be_hidden()
    expect(page.locator('#horseman-peek')).to_be_visible()
    expect(page.locator('#horseman-hint')).to_be_hidden()


def test_hint_fits_beside_the_logo_on_any_screen(page, halloween_server):
    """Ширина текста зависит от шрифтов машины (свой шрифт сайта не в git, в
    CI шрифты другие): проверяем и с «расширенным» шрифтом — реплика должна
    переноситься, а не вылезать за экран или на заголовок."""
    page.emulate_media(reduced_motion='reduce')
    page.goto(halloween_server)
    for spacing in ('normal', '0.08em'):
        page.add_style_tag(content=f'body {{ letter-spacing: {spacing}; }}')
        for width in (320, 390, 1280):
            page.set_viewport_size({'width': width, 'height': 800})
            page.wait_for_function(
                'width => innerWidth === width && document.documentElement.scrollWidth <= width',
                arg=width, timeout=3000)
            hint = page.locator('#horseman-hint').bounding_box()
            assert hint['x'] + hint['width'] <= width, (spacing, width)
            for other in ('.brand-title:not(.chase-only)', LOGO):
                box = page.locator(other).bounding_box()
                assert (hint['y'] + hint['height'] <= box['y']
                        or hint['x'] >= box['x'] + box['width']), (spacing, width, other)


def test_hover_reveal_is_desktop_only(browser, page, halloween_server):
    """На телефоне тап сначала «наводит»: если от этого что-то проявится,
    iOS Safari не отправит клик. Поэтому подсказка наведением — только там,
    где наведение настоящее, а первый тап сразу включает режим."""
    page.goto(halloween_server)
    page.locator(LOGO).hover()
    assert page.locator('#horseman-peek').evaluate(
        'el => getComputedStyle(el).animationName') == 'none'
    expect(page.locator(NIGHT_LOGO)).to_have_css('opacity', '1')

    context = browser.new_context(viewport={'width': 390, 'height': 844},
                                  is_mobile=True, has_touch=True)
    stub_js = (Path(__file__).parent / 'ymaps_stub.js').read_text()
    context.route('**/api-maps.yandex.ru/**', lambda route: route.fulfill(
        status=200, content_type='application/javascript', body=stub_js))
    context.route('**/mc.yandex.ru/**', lambda route: route.abort())
    phone = context.new_page()
    try:
        phone.goto(halloween_server)
        assert phone.evaluate("matchMedia('(hover: hover)').matches") is False
        phone.locator(LOGO).hover()
        assert phone.locator('#horseman-peek').evaluate(
            'el => getComputedStyle(el).animationName') == 'horseman-haunt'
        phone.locator(LOGO).tap()
        expect(phone.locator('#chase-title')).to_be_visible(timeout=3000)
    finally:
        context.close()


def test_second_click_on_horseman_returns_regular_game(page, halloween_server):
    _open_chase(page, halloween_server)
    page.locator('#horseman-peek').dispatch_event('click')

    expect(page.locator('.brand-title:not(.chase-only)')).to_have_text('Петербургский следопыт')
    expect(page.locator('#chase-title')).to_be_hidden()
    expect(page.locator('html')).not_to_have_class(re.compile('theme-chase'))
    expect(page.locator('#start-btn')).to_have_text('Начать игру')
    expect(page.locator('#horseman-peek')).to_have_attribute(
        'aria-label', re.compile('Включить'))
    expect(page.locator('#daily-btn')).to_be_enabled()
    expect(page.locator('#timer-unlimited')).to_be_enabled()
    # «Без лимита» был выбран до погони — он и возвращается.
    expect(page.locator('input[name="round-time"]:checked')).to_have_value('0')

    with page.expect_request(lambda request: request.url.endswith('/api/game/start')) as start:
        page.locator('#start-btn').click()
    assert start.value.post_data_json['time_limit'] is None
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    expect(page.locator('.game-stage')).not_to_have_class(re.compile('chase-mode'))


def test_flood_switches_mode_under_the_wave(page, halloween_server):
    page.goto(halloween_server)
    expect(page.locator('#chase-flood')).not_to_have_class(re.compile('run'))
    page.locator(LOGO).click()
    expect(page.locator('#chase-flood')).to_have_class(re.compile('run'))
    expect(page.locator('#chase-title')).to_be_visible(timeout=3000)
    expect(page.locator('#chase-flood')).not_to_have_class(re.compile('run'), timeout=3000)
    expect(page.locator('#start-btn')).to_have_text('Бежать')


def test_horseman_catches_player_and_next_round_starts_far_away(page, halloween_server):
    guesses = _record_guesses(page)
    _shorten_rounds(page, 1500)
    _open_chase(page, halloween_server)
    page.locator('#start-btn').click()

    expect(page.locator('#chase-caught')).to_be_visible(timeout=10000)
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#result-title')).to_have_text('Всадник догнал!')
    expect(page.locator('#result-miss')).to_have_text(
        'Всадник догнал тебя раньше, чем ты отметил точку')
    # Сцена короткая и не мешает нажать «Продолжить».
    expect(page.locator('#chase-caught')).to_be_hidden(timeout=4000)
    assert len(guesses) == 1
    assert guesses[0]['timed_out'] is True

    page.unroute('**/api/game/ready')
    page.locator('#next-round-btn').click()
    expect(page.locator('#current-round')).to_have_text('2')
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    # Новый раунд — погоня заново: первая фраза и честный заголовок.
    expect(page.locator('#chase-whisper')).to_have_text(FIRST_WHISPER)
    page.locator('#map').click(position={'x': 150, 'y': 120})
    page.locator('#guess-btn').click()
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    expect(page.locator('#result-title')).not_to_have_text('Всадник догнал!')


def test_point_marked_before_catch_still_counts(page, halloween_server):
    guesses = _record_guesses(page)
    # С запасом на загрузку карты под нагрузкой: точку надо успеть отметить.
    _shorten_rounds(page, 8000)
    _open_chase(page, halloween_server)
    _start_chase_round(page)
    page.locator('#map').click(position={'x': 150, 'y': 120})
    expect(page.locator('#guess-btn')).to_be_enabled()

    # Ответа нет — его отправит истечение времени. Заголовок «догнал» при
    # засчитанной точке ставит только сцена поимки.
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'), timeout=15000)
    expect(page.locator('#result-title')).to_have_text('Всадник догнал!')
    expect(page.locator('#result-distance')).to_be_visible()
    assert len(guesses) == 1
    assert 'latitude' in guesses[0] and not guesses[0].get('timed_out')


def test_regular_timed_game_has_no_chase_in_season(page, halloween_server):
    page.goto(halloween_server)
    page.get_by_text('1 мин', exact=True).click()
    page.locator('#start-btn').click()
    expect(page.locator('#panorama-player .stub-pano')).to_be_visible(timeout=10000)
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    expect(page.locator('#timer-chip')).to_be_visible()
    expect(page.locator('#timer-chip span.timer-icon')).to_be_visible()
    expect(page.locator('#chase-layer')).to_be_hidden()
    expect(page.locator('#chase-whisper')).not_to_have_class(re.compile('show'))
    assert page.locator('#panorama-player canvas').evaluate(
        'element => getComputedStyle(element).filter') == 'none'


def test_challenge_link_hides_the_horseman(page, halloween_server):
    token = _author_challenge_token(halloween_server, difficulty='center', time_limit=60)
    # Без анимаций реплика видна всегда — скрыть её может только челлендж.
    page.emulate_media(reduced_motion='reduce')
    page.goto(f'{halloween_server}/?challenge={token}')
    expect(page.locator('#challenge-banner')).to_contain_text('Автор')
    # Погоня — своя игра, а не способ сыграть чужой челлендж.
    expect(page.locator('#horseman-peek')).to_be_hidden()
    expect(page.locator('#horseman-hint')).to_be_hidden()
    page.locator(LOGO).click()
    expect(page.locator('#chase-title')).to_be_hidden()


def test_off_season_page_has_no_halloween(page, server):
    page.goto(server)
    expect(page.locator('#start-btn')).to_be_visible()
    for selector in ('#horseman-peek', '#horseman-hint', '#chase-title', '#chase-flood', '#chase-layer'):
        expect(page.locator(selector)).to_have_count(0)
    expect(page.locator('html')).not_to_have_class(re.compile('theme-chase'))
