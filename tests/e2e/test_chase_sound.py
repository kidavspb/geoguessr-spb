"""Хэллоуин: звук «Ночи Медного всадника» — волна, галоп, сердце, поимка."""
import re
from pathlib import Path

from playwright.sync_api import expect

from test_chase import LOGO, NIGHT_LOGO, _open_chase, _shorten_rounds, _start_chase_round

AUDIO = re.compile(r'/static/audio/halloween/(\w+)\.mp3')
ROUND_FILES = {'gallop', 'heartbeat', 'thud', 'gong', 'snort'}


def _with_audio(page):
    """Web Audio — заглушка с журналом в window.__audio, файлы — текст с
    именем звука. Возвращает список запрошенных файлов."""
    requested = []
    page.add_init_script(path=str(Path(__file__).parent / 'audio_stub.js'))

    def serve(route):
        name = AUDIO.search(route.request.url).group(1)
        requested.append(name)
        route.fulfill(status=200, content_type='audio/mpeg', body=name)

    page.route(AUDIO, serve)
    return requested


def _started(page, name=None):
    return page.evaluate(
        'name => window.__audio.started.filter(s => !name || s.name === name)', name)


def _wait_for(page, condition, timeout_ms=5000):
    for _ in range(timeout_ms // 50):
        if condition():
            return
        page.wait_for_timeout(50)
    assert condition()


def _gaps(page, name):
    times = [sound['when'] for sound in _started(page, name)]
    return [later - earlier for earlier, later in zip(times, times[1:])]


def test_regular_game_in_season_is_silent(page, halloween_server):
    requested = _with_audio(page)
    page.emulate_media(reduced_motion='reduce')
    page.goto(halloween_server)
    page.get_by_text('1 мин', exact=True).click()
    page.locator('#start-btn').click()
    expect(page.locator('#photo-overlay')).to_be_hidden(timeout=10000)
    expect(page.locator('#timer-chip')).to_be_visible()
    page.wait_for_timeout(800)
    expect(page.locator('#chase-sound-btn')).to_be_hidden()
    assert page.evaluate('window.__audio.contexts') == 0
    assert requested == []


def test_wave_sound_loads_on_approach_and_plays_on_click(page, halloween_server):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    requested = _with_audio(page)
    page.emulate_media(reduced_motion='reduce')
    page.goto(halloween_server)

    # Наведение только загружает волну: звук без жеста браузер не пустит.
    page.locator(LOGO).hover()
    _wait_for(page, lambda: sorted(requested) == ['thud', 'water'])
    assert page.evaluate('window.__audio.contexts') == 0

    page.locator(LOGO).click()
    expect(page.locator('#chase-title')).to_be_visible()
    page.wait_for_function(
        "() => ['water', 'thud'].every(n => window.__audio.started.some(s => s.name === n))")
    assert page.evaluate('window.__audio.contexts') == 1
    # Звуки раунда качаются после входа в режим, каждый файл — один раз.
    _wait_for(page, lambda: set(requested) == {'water'} | ROUND_FILES)
    assert len(requested) == len(set(requested))

    # Обратная волна — только вода, без удара бронзы.
    page.locator(NIGHT_LOGO).click()
    expect(page.locator('#chase-title')).to_be_hidden()
    page.wait_for_function(
        "() => window.__audio.started.filter(s => s.name === 'water').length === 2")
    page.wait_for_timeout(900)
    assert len(_started(page, 'thud')) == 1
    assert errors == []


def test_gallop_is_far_and_slow_at_the_start_of_a_round(page, halloween_server):
    _with_audio(page)
    _open_chase(page, halloween_server)
    _start_chase_round(page)
    page.wait_for_function(
        "() => window.__audio.started.filter(s => s.name === 'gallop').length >= 4")
    gaps = _gaps(page, 'gallop')
    assert all(0.62 < gap < 0.72 for gap in gaps), gaps
    assert _started(page, 'heartbeat') == []
    # Шаги — секундные куски одного файла, по одному за раз.
    strides = _started(page, 'gallop')
    assert {stride['offset'] for stride in strides} <= {0, 1, 2, 3}
    assert all(stride['duration'] == 1 for stride in strides)


def test_heart_joins_and_gallop_closes_in_at_the_end(page, halloween_server):
    _with_audio(page)
    _shorten_rounds(page, 6000)
    _open_chase(page, halloween_server)
    _start_chase_round(page)
    # До ответа /ready часы раунда ещё полные, а уже поставленный шаг держит
    # прежний интервал — смотрим шаги через секунду после первого удара сердца.
    page.wait_for_function("""() => {
        const started = window.__audio.started;
        const heart = started.find(s => s.name === 'heartbeat');
        return heart && started.filter(
            s => s.name === 'gallop' && s.when > heart.when + 1).length >= 3;
    }""")
    heart_from = _started(page, 'heartbeat')[0]['when']
    times = [s['when'] for s in _started(page, 'gallop') if s['when'] > heart_from + 1]
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    assert len(gaps) >= 2 and all(gap < 0.56 for gap in gaps), gaps


def test_answer_in_time_silences_the_gallop(page, halloween_server):
    _with_audio(page)
    _open_chase(page, halloween_server)
    _start_chase_round(page)
    page.wait_for_function("() => window.__audio.started.some(s => s.name === 'gallop')")
    page.locator('#map').click(position={'x': 150, 'y': 120})
    page.locator('#guess-btn').click()
    expect(page.locator('#result-screen')).to_have_class(re.compile('active'))
    strides = len(_started(page, 'gallop'))
    page.wait_for_timeout(1000)
    assert len(_started(page, 'gallop')) == strides
    assert _started(page, 'gong') == [] and _started(page, 'snort') == []


def test_caught_scene_rings_bronze_and_the_horse_snorts(page, halloween_server):
    _with_audio(page)
    _shorten_rounds(page, 1500)
    _open_chase(page, halloween_server)
    page.locator('#start-btn').click()
    expect(page.locator('#chase-caught')).to_be_visible(timeout=10000)
    page.wait_for_function(
        "() => ['gong', 'snort'].every(n => window.__audio.started.some(s => s.name === n))")
    # Удар бронзы — при входе в режим и при поимке.
    assert len(_started(page, 'thud')) == 2
    strides = len(_started(page, 'gallop'))
    page.wait_for_timeout(800)
    assert len(_started(page, 'gallop')) == strides


def test_sound_button_mutes_and_is_remembered(page, halloween_server):
    requested = _with_audio(page)
    _open_chase(page, halloween_server)
    _start_chase_round(page)
    button = page.locator('#chase-sound-btn')
    expect(button).to_be_visible()
    expect(button).to_have_attribute('aria-pressed', 'true')
    page.wait_for_function("() => window.__audio.started.some(s => s.name === 'gallop')")
    button.click()
    expect(button).to_have_attribute('aria-pressed', 'false')
    expect(button).to_have_attribute('title', 'Включить звук')
    strides = len(_started(page, 'gallop'))
    page.wait_for_timeout(800)
    assert len(_started(page, 'gallop')) == strides

    # Выбор помнит устройство: после перезагрузки волна молчит и ничего не качает.
    requested.clear()
    _open_chase(page, halloween_server)
    _start_chase_round(page)
    expect(button).to_have_attribute('aria-pressed', 'false')
    page.wait_for_timeout(800)
    assert page.evaluate('window.__audio.contexts') == 0
    assert requested == []
    # Включить можно прямо в раунде — галоп вступает.
    button.click()
    expect(button).to_have_attribute('aria-pressed', 'true')
    page.wait_for_function("() => window.__audio.started.some(s => s.name === 'gallop')")


def _overlap(a, b):
    return (a['x'] < b['x'] + b['width'] and b['x'] < a['x'] + a['width']
            and a['y'] < b['y'] + b['height'] and b['y'] < a['y'] + a['height'])


def test_sound_button_covers_neither_home_button_nor_whisper(page, halloween_server):
    _with_audio(page)
    for width in (1280, 390):
        page.set_viewport_size({'width': width, 'height': 800})
        _open_chase(page, halloween_server)
        _start_chase_round(page)
        expect(page.locator('#chase-whisper')).to_have_class(re.compile('show'))
        sound = page.locator('#chase-sound-btn').bounding_box()
        for other in ('#pano-home', '#chase-whisper', '.hud-top'):
            assert not _overlap(sound, page.locator(other).bounding_box()), (width, other)
