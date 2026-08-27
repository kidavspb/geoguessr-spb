"""GeoGuessr СПб («Петербургский следопыт») — Flask-приложение и API.

Структура проекта:
  game_logic.py — чистая игровая логика (константы, расчёты, валидация)
  pool.py       — пул проверенных точек с панорамами
  geocoder.py   — обратное геокодирование (адрес точки ответа)
  models.py     — модели БД
  app.py        — конфигурация, миграции, rate limiting, HTTP-роуты
"""
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, g, render_template, jsonify, request, session, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from flask_migrate import Migrate, upgrade as _alembic_upgrade, stamp as _alembic_stamp
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import event

from models import db, GameSession, GameRound, utcnow
# Ре-экспорт логики в неймспейс app: роуты используют её напрямую,
# а тесты обращаются к функциям и константам через модуль app.
from game_logic import (
    ROUNDS_PER_GAME, MAX_SCORE_PER_ROUND, MAX_DISTANCE_KM,
    MAX_SKIPS_PER_ROUND, MAX_ACTUAL_POINT_DRIFT_KM,
    TIME_LIMIT_GRACE_SECONDS, TIME_LIMIT_MIN, TIME_LIMIT_MAX,
    SPB_BOUNDS, SPB_CENTER, DIFFICULTY_SETTINGS,
    difficulty_name, generate_random_point, haversine_distance,
    calculate_score, parse_coords, parse_time_limit,
)
from pool import POOL_MIN_SIZE, POOL_USE_PROBABILITY, POOL_RADIUS_KM, \
    choose_round_points, choose_round_candidates, add_verified_point, mark_point_failed
from geocoder import reverse_geocode
from daily import DAILY_DIFFICULTY, today_msk, get_or_create_daily, daily_points
from stats import difficulty_percentile
from models import VerifiedPoint

# Загружаем переменные окружения из .env
load_dotenv()


def _env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
# Брендинг переключается независимо от debug-режима: dev-сайт получает
# экспериментальные иконки только при явном FAVICON_VARIANT=dev. Любое другое
# значение безопасно оставляет production-набор.
app.config['FAVICON_VARIANT'] = (
    'dev' if os.environ.get('FAVICON_VARIANT', '').strip().lower() == 'dev' else 'prod'
)
# URI базы можно переопределить через DATABASE_URL (12-factor): удобно для
# тестов (отдельная/временная БД) и для смены СУБД, не трогая код.
# По умолчанию — sqlite-файл в папке instance/.
database_uri = os.environ.get('DATABASE_URL', 'sqlite:///geoguessr_spb.db')
app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Два gunicorn worker'а могут почти одновременно фиксировать догадку,
# метрику и новую точку пула. SQLite ждёт освобождения writer lock вместо
# мгновенного OperationalError; pool_pre_ping полезен и при внешней СУБД.
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
if database_uri.startswith('sqlite:'):
    app.config['SQLALCHEMY_ENGINE_OPTIONS']['connect_args'] = {'timeout': 15}
# Фронтенд — ES-модули: импорты не имеют query-версии, поэтому статика
# отдаётся с ревалидацией кэша (условные запросы → дешёвые 304)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Безопасность cookie сессии.
# На проде (HTTPS) держим Secure=True; для локальной разработки можно
# выставить SESSION_COOKIE_SECURE=false в .env, иначе cookie не установится по http.
app.config.update(
    SESSION_COOKIE_SECURE=_env_bool('SESSION_COOKIE_SECURE', True),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

# Приложение работает за обратным прокси (nginx) — доверяем одному прокси,
# чтобы корректно видеть схему (https) и реальный IP клиента.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# API ключ Яндекс Карт (для JS API на странице)
YANDEX_MAPS_API_KEY = os.environ.get('YANDEX_MAPS_API_KEY', '')
# Ключ модератора пула точек (страница /admin). Не задан — админка выключена.
ADMIN_KEY = os.environ.get('ADMIN_KEY', '')
# Номер счётчика Яндекс Метрики; не задан — аналитика выключена.
METRIKA_ID = os.environ.get('METRIKA_ID', '')

db.init_app(app)
migrate = Migrate(app, db, directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'migrations'))


if database_uri.startswith('sqlite:'):
    with app.app_context():
        @event.listens_for(db.engine, 'connect')
        def _configure_sqlite(dbapi_connection, _connection_record):
            """WAL позволяет чтениям не ждать записи; NORMAL безопасен с WAL."""
            if not isinstance(dbapi_connection, sqlite3.Connection):
                return
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute('PRAGMA foreign_keys=ON')
                cursor.execute('PRAGMA busy_timeout=15000')
                cursor.execute('PRAGMA journal_mode=WAL')
                cursor.execute('PRAGMA synchronous=NORMAL')
            finally:
                cursor.close()

# Rate limiting: in-memory (на процесс). Защищает от забивания лидерборда
# мусорными сессиями и от скриптового перебора, а не от распределённых атак.
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri='memory://',
    default_limits=[],
    enabled=_env_bool('RATELIMIT_ENABLED', True),
)

# Ревизия, соответствующая схеме последней версии до перехода на Alembic
# (создавалась через db.create_all()).
BASELINE_REVISION = '5f7875fb0c81'


def _run_migrations():
    """Привести БД к актуальной схеме через Alembic.

    Три сценария:
      * пустая БД — миграции создают всё с нуля;
      * БД времён db.create_all() (без alembic_version) — помечаем её нулевой
        ревизией и накатываем остальное;
      * БД уже под Alembic — обычный upgrade (no-op, если она актуальна).
    """
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    if 'game_sessions' in tables and 'alembic_version' not in tables:
        _alembic_stamp(revision=BASELINE_REVISION)
        app.logger.info('БД без Alembic: помечена ревизией %s', BASELINE_REVISION)
    _alembic_upgrade()


# Автоматически догоняем схему при старте. При multi-worker запуске gunicorn
# используйте --preload либо выключите (AUTO_MIGRATE=false) и запускайте
# `flask db upgrade` отдельным шагом деплоя.
if _env_bool('AUTO_MIGRATE', True):
    with app.app_context():
        _run_migrations()


# --------------------------------------------------------------------------
# Помощники роутов
# --------------------------------------------------------------------------

def _aware_utc(dt):
    """SQLite возвращает naive datetime — считаем, что это UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _current_game():
    """Игровая сессия текущего игрока (по game_id из подписанной cookie)."""
    game_id = session.get('game_id')
    if not game_id:
        return None
    return db.session.get(GameSession, game_id)


def _load_active_round():
    """Игра и строка её текущего раунда, либо (None, None, ответ-ошибка).

    Общая проверка для location / skip / set_actual_point / guess.
    """
    game = _current_game()
    if game is None:
        return None, None, (jsonify({'error': 'Игра не начата'}), 400)

    if game.completed_at is not None or (game.current_round or 0) >= ROUNDS_PER_GAME:
        return None, None, (jsonify({'error': 'Игра завершена', 'game_over': True}), 400)

    rnd = GameRound.query.filter_by(
        session_id=game.id, round_number=(game.current_round or 0) + 1
    ).first()
    if rnd is None or rnd.gen_latitude is None:
        return None, None, (jsonify({'error': 'Игра не начата'}), 400)

    return game, rnd, None


def _location_payload(game, rnd):
    """Публичные данные точки, достаточные клиенту для поиска панорамы."""
    return {
        'round_id': rnd.id,
        'round': rnd.round_number,
        'total_rounds': ROUNDS_PER_GAME,
        'time_limit': game.time_limit,
        'no_move': bool(game.no_move),
        'latitude': rnd.gen_latitude,
        'longitude': rnd.gen_longitude,
        'source': rnd.location_source or 'legacy',
        'max_panorama_drift_km': MAX_ACTUAL_POINT_DRIFT_KM,
        # Версия кандидата внутри того же round_id. Нужна, чтобы безопасно
        # повторить skip после потерянного HTTP-ответа, не перескочив ещё раз.
        'location_version': rnd.skips or 0,
    }


def _round_from_payload(data, *, allow_answered=False, require_active=True):
    """Раунд из тела запроса с защитой от запоздавших сетевых ответов.

    Старые клиенты без ``round_id`` временно поддерживаются через текущий
    активный раунд. Новый фронтенд всегда передаёт id, поэтому повтор запроса
    никогда не сможет случайно изменить следующий раунд.
    """
    game = _current_game()
    if game is None:
        return None, None, (jsonify({'error': 'Игра не начата'}), 400)

    round_id = data.get('round_id') if isinstance(data, dict) else None
    if round_id in (None, ''):
        return _load_active_round()
    try:
        round_id = int(round_id)
    except (TypeError, ValueError):
        return None, None, (jsonify({'error': 'Некорректный идентификатор раунда'}), 400)

    rnd = db.session.get(GameRound, round_id)
    if rnd is None or rnd.session_id != game.id:
        return None, None, (jsonify({'error': 'Раунд устарел или не найден'}), 409)

    if rnd.answered_at is not None and allow_answered:
        return game, rnd, None
    if rnd.answered_at is not None:
        return None, None, (jsonify({'error': 'Раунд уже сыгран'}), 409)

    if require_active:
        active_number = (game.current_round or 0) + 1
        if game.completed_at is not None or rnd.round_number != active_number:
            return None, None, (jsonify({'error': 'Раунд устарел'}), 409)
    return game, rnd, None


def _scoring_point(rnd):
    """Точка, по которой считаются очки: панорама, а если её нет — серверная."""
    if rnd.actual_latitude is not None and rnd.actual_longitude is not None:
        return rnd.actual_latitude, rnd.actual_longitude
    return rnd.gen_latitude, rnd.gen_longitude


def _round_result_payload(game, rnd, *, replayed=False, percentile=None):
    """Единый ответ для первого и повторного POST /guess."""
    actual_lat, actual_lon = _scoring_point(rnd)
    distance = rnd.distance_km
    game_over = game.completed_at is not None or (game.current_round or 0) >= ROUNDS_PER_GAME
    payload = {
        'round_id': rnd.id,
        'correct_location': {'latitude': actual_lat, 'longitude': actual_lon},
        'guess': {
            'latitude': rnd.guess_latitude,
            'longitude': rnd.guess_longitude,
        } if rnd.guess_latitude is not None else None,
        'address': rnd.address,
        'difficulty_percentile': percentile,
        'timed_out': bool(rnd.timed_out),
        'distance_km': round(distance, 2) if distance is not None else None,
        'distance_m': int(distance * 1000) if distance is not None else None,
        'score': rnd.score or 0,
        'total_score': game.total_score or 0,
        'round': rnd.round_number,
        'is_game_over': game_over,
        'replayed': replayed,
    }
    # Потерянный ответ можно безопасно повторить: если игра всё ещё ровно на
    # границе этого раунда, сразу возвращаем следующую точку для прогрева.
    if not game_over and (game.current_round or 0) == rnd.round_number:
        next_rnd = GameRound.query.filter_by(
            session_id=game.id, round_number=rnd.round_number + 1
        ).first()
        if next_rnd is not None:
            payload['next_location'] = _location_payload(game, next_rnd)
    return payload


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({'error': 'Слишком много запросов. Попробуйте чуть позже.'}), 429


@app.before_request
def _start_request_timer():
    if request.path.startswith('/api/'):
        g.api_started_at = time.perf_counter()


@app.after_request
def _report_request_timing(response):
    """Отделить медленный сервер от медленного SDK/тайлов в диагностике."""
    if request.path.startswith('/static/') and request.args.get('v'):
        # CSS и entry-модуль имеют версию ассетов в URL: повторный визит не
        # должен даже ревалидировать их. Импорты без версии остаются no-cache.
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    started = getattr(g, 'api_started_at', None)
    if started is None:
        return response
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers['Server-Timing'] = f'app;dur={duration_ms:.1f}'
    if duration_ms >= 1000:
        app.logger.warning(
            'Медленный API: %s %s -> %s за %.0f мс',
            request.method, request.path, response.status_code, duration_ms,
        )
    return response


@app.context_processor
def inject_asset_version():
    """Кэш-бастер ассетов: версия — свежайший mtime важных файлов статики.

    Считается на каждый запрос, чтобы правки статики подхватывались
    браузерами и без перезапуска сервиса.
    """
    version = 0
    for rel in ('css/style.css', 'js/main.js', 'js/panorama.js', 'js/maps.js',
                'js/api.js', 'js/state.js', 'js/utils.js', 'js/sdk.js',
                'manifest.json', 'manifest-dev.json',
                'favicons/prod/favicon.svg', 'favicons/prod/favicon.ico',
                'favicons/dev/favicon.svg', 'favicons/dev/favicon.ico'):
        path = os.path.join(app.static_folder, rel)
        try:
            version = max(version, os.stat(path).st_mtime_ns)
        except OSError:
            pass
    return {'asset_version': version}


def favicon_asset(filename):
    """Путь к favicon из набора, выбранного для текущего окружения."""
    variant = app.config['FAVICON_VARIANT']
    return f'favicons/{variant}/{filename}'


def manifest_asset():
    """PWA-манифест с иконками выбранного окружения."""
    return 'manifest-dev.json' if app.config['FAVICON_VARIANT'] == 'dev' else 'manifest.json'


app.jinja_env.globals.update(
    favicon_asset=favicon_asset,
    manifest_asset=manifest_asset,
)


# --------------------------------------------------------------------------
# Страницы
# --------------------------------------------------------------------------

@app.route('/')
def index():
    """Главная страница с игрой.

    Для челлендж-ссылок (?challenge=TOKEN) подставляем OG-теги с именем и
    счётом автора: мессенджеры рендерят превью без выполнения JS, поэтому
    это делается на сервере.
    """
    og_title = 'Петербургский следопыт'
    og_description = ('Тебя высаживают в случайной точке Петербурга — осмотрись '
                      'на панораме и угадай на карте, где ты.')

    token = (request.args.get('challenge') or '').strip()
    if token:
        source = GameSession.query.filter(
            GameSession.challenge_token == token,
            GameSession.completed_at.isnot(None),
        ).first()
        if source is not None:
            og_title = (f'{source.player_name} набрал {source.total_score} очков '
                        f'в «Петербургском следопыте»')
            og_description = 'Тебе достанутся те же 5 панорам. Сможешь точнее?'

    return render_template('index.html',
                           yandex_api_key=YANDEX_MAPS_API_KEY,
                           metrika_id=METRIKA_ID,
                           og_title=og_title,
                           og_description=og_description)


@app.route('/favicon.ico')
def favicon():
    """Фавиконка для запросов к корню сайта (браузеры/краулеры идут на /favicon.ico)."""
    return send_from_directory(app.static_folder, favicon_asset('favicon.ico'),
                               mimetype='image/vnd.microsoft.icon')


# --------------------------------------------------------------------------
# Игровое API
# --------------------------------------------------------------------------

@app.route('/api/game/start', methods=['POST'])
@limiter.limit('10 per minute')
def start_game():
    """Начать новую игру.

    Всё состояние (точки раундов, прогресс) хранится на сервере; в cookie
    клиента остаётся только id игры. При переданном challenge_token игра
    стартует с теми же точками, что у автора челленджа.
    """
    try:
        data = request.get_json(silent=True) or {}
        player_name = (str(data.get('player_name') or 'Аноним')).strip()[:50] or 'Аноним'

        source = None
        daily = None
        is_daily = bool(data.get('daily'))
        challenge_token = (str(data.get('challenge_token') or '')).strip()

        if is_daily:
            # Ежедневный вызов: общий набор точек, фиксированный режим.
            # Одна попытка в день (на уровне cookie-сессии браузера).
            previous = session.get('daily_game')
            if previous and previous.get('date') == today_msk().isoformat():
                prev_game = db.session.get(GameSession, previous.get('game_id'))
                if prev_game is not None:
                    if prev_game.completed_at is not None:
                        return jsonify({
                            'error': 'Сегодня ты уже играл в вызов дня',
                            'already_played': True,
                            'total_score': prev_game.total_score,
                        }), 409
                    # Недоигранный вызов — продолжаем его же
                    session['game_id'] = prev_game.id
                    active_round = GameRound.query.filter_by(
                        session_id=prev_game.id,
                        round_number=(prev_game.current_round or 0) + 1,
                    ).first()
                    resumed_payload = {
                        'game_id': prev_game.id,
                        'total_rounds': ROUNDS_PER_GAME,
                        'difficulty': prev_game.difficulty,
                        'difficulty_name': difficulty_name(prev_game.difficulty),
                        'time_limit': prev_game.time_limit,
                        'daily': True,
                        'resumed': True,
                        'total_score': prev_game.total_score,
                        'message': 'Продолжаем вызов дня!'
                    }
                    if active_round is not None:
                        resumed_payload['location'] = _location_payload(prev_game, active_round)
                    return jsonify(resumed_payload)
            daily = get_or_create_daily()
            difficulty = DAILY_DIFFICULTY
            time_limit = None
            no_move = False  # вызов дня — в стандартных правилах для всех
        elif challenge_token:
            source = GameSession.query.filter(
                GameSession.challenge_token == challenge_token,
                GameSession.completed_at.isnot(None),
            ).first()
            if source is None:
                return jsonify({'error': 'Челлендж не найден или игра ещё не завершена'}), 404
            difficulty = source.difficulty
            time_limit = source.time_limit
            no_move = bool(source.no_move)
        else:
            difficulty = data.get('difficulty', 'medium')  # center, medium, hard, hardcore
            if difficulty not in DIFFICULTY_SETTINGS:
                difficulty = 'medium'
            time_limit = parse_time_limit(data.get('time_limit'))
            no_move = bool(data.get('no_move'))

        game_session = GameSession(
            player_name=player_name,
            difficulty=difficulty,
            time_limit=time_limit,
            no_move=no_move,
            current_round=0,
            challenge_token=secrets.token_urlsafe(12),
            challenged_from_id=source.id if source else None,
            daily_date=daily.date if daily else None,
        )
        db.session.add(game_session)
        db.session.flush()  # получаем id до создания раундов

        if daily is not None:
            for i, (lat, lon) in enumerate(daily_points(daily), start=1):
                db.session.add(GameRound(
                    session_id=game_session.id,
                    round_number=i,
                    gen_latitude=lat,
                    gen_longitude=lon,
                    location_source='daily',
                ))
        elif source is not None:
            # Копируем точки исходной игры: и серверные, и найденные панорамы,
            # чтобы очки обоих игроков считались по одним и тем же местам.
            source_rounds = sorted(source.rounds, key=lambda r: r.round_number)[:ROUNDS_PER_GAME]
            for r in source_rounds:
                db.session.add(GameRound(
                    session_id=game_session.id,
                    round_number=r.round_number,
                    # У завершённой исходной игры точная позиция панорамы уже
                    # известна — не заставляем друга искать её снова вокруг
                    # старой сгенерированной координаты.
                    gen_latitude=r.actual_latitude if r.actual_latitude is not None else r.gen_latitude,
                    gen_longitude=r.actual_longitude if r.actual_longitude is not None else r.gen_longitude,
                    actual_latitude=r.actual_latitude,
                    actual_longitude=r.actual_longitude,
                    location_source='challenge',
                ))
        else:
            candidates = choose_round_candidates(difficulty, ROUNDS_PER_GAME)
            for i, (lat, lon, point_source) in enumerate(candidates, start=1):
                db.session.add(GameRound(
                    session_id=game_session.id,
                    round_number=i,
                    gen_latitude=lat,
                    gen_longitude=lon,
                    location_source=point_source,
                ))

        db.session.commit()

        session['game_id'] = game_session.id
        if daily is not None:
            session['daily_game'] = {'date': daily.date.isoformat(),
                                     'game_id': game_session.id}

        app.logger.info(f'Игра начата: game_id={game_session.id}, player={player_name}, '
                        f'difficulty={difficulty}, time_limit={time_limit}, '
                        f'daily={"да" if daily else "нет"}, '
                        f'challenge={"да" if source else "нет"}')

        response = {
            'game_id': game_session.id,
            'total_rounds': ROUNDS_PER_GAME,
            'difficulty': difficulty,
            'difficulty_name': difficulty_name(difficulty),
            'time_limit': time_limit,
            'no_move': no_move,
            'daily': daily is not None,
            'message': 'Игра началась!'
        }
        first_round = GameRound.query.filter_by(
            session_id=game_session.id, round_number=1
        ).first()
        if first_round is not None:
            # Экономим отдельный GET /location на первом кадре. Таймер начнёт
            # отдельный /ready только после реального открытия панорамы.
            response['location'] = _location_payload(game_session, first_round)
        if source is not None:
            response['challenge'] = {
                'opponent_name': source.player_name,
                'opponent_score': source.total_score,
            }
        return jsonify(response)
    except Exception:
        app.logger.exception('Ошибка при старте игры')
        db.session.rollback()
        return jsonify({'error': 'Ошибка сервера'}), 500


@app.route('/api/game/location', methods=['GET'])
def get_current_location():
    """Получить текущую локацию для угадывания"""
    game, rnd, error = _load_active_round()
    if error:
        return error

    # Выдача координат больше не запускает таймер: медленное соединение не
    # должно съедать игровое время. Клиент вызовет /ready после открытия Player.
    # Параметр ?peek=1 оставлен для совместимости со старым фронтендом.
    return jsonify(_location_payload(game, rnd))


@app.route('/api/game/ready', methods=['POST'])
def round_ready():
    """Зафиксировать момент, когда панорама действительно появилась на экране."""
    data = request.get_json(silent=True) or {}
    game, rnd, error = _round_from_payload(data)
    if error:
        return error

    if rnd.started_at is None:
        rnd.started_at = utcnow()
        db.session.commit()

    started = _aware_utc(rnd.started_at)
    deadline_ms = None
    if game.time_limit:
        deadline_ms = int((started + timedelta(seconds=game.time_limit)).timestamp() * 1000)
    return jsonify({
        'success': True,
        'round_id': rnd.id,
        'started_at_ms': int(started.timestamp() * 1000),
        'deadline_ms': deadline_ms,
        'time_limit': game.time_limit,
    })


@app.route('/api/game/skip_location', methods=['POST'])
@limiter.limit('60 per minute')
def skip_location():
    """Перегенерировать точку текущего раунда.

    Нужно, когда в сгенерированной точке нет панорамы Яндекса. Количество
    перегенераций ограничено и на сервере: иначе точку можно рероллить,
    пока не выпадет знакомое место.
    """
    data = request.get_json(silent=True) or {}
    game, rnd, error = _round_from_payload(data)
    if error:
        return error

    expected_version = data.get('location_version')
    if expected_version not in (None, ''):
        try:
            expected_version = int(expected_version)
        except (TypeError, ValueError):
            return jsonify({'error': 'Некорректная версия точки'}), 400
        current_version = rnd.skips or 0
        if expected_version < current_version:
            # Первый POST уже сработал, но ответ потерялся: возвращаем тот же
            # новый кандидат и не расходуем ещё один skip.
            payload = _location_payload(game, rnd)
            payload['replayed'] = True
            return jsonify(payload)
        if expected_version > current_version:
            return jsonify({'error': 'Версия точки устарела'}), 409

    if (rnd.skips or 0) >= MAX_SKIPS_PER_ROUND:
        return jsonify({'error': 'Лимит перегенераций точки для этого раунда исчерпан'}), 429

    reason = str(data.get('reason') or 'no_coverage')[:30]
    excluded_coords = [
        (other.gen_latitude, other.gen_longitude)
        for other in game.rounds
        if other.gen_latitude is not None and other.gen_longitude is not None
    ]
    # Пустой успешный ответ locate означает, что покрытие действительно
    # исчезло. Сетевой сбой не должен отравлять и постепенно удалять весь пул.
    if reason == 'no_coverage':
        # Сигнал пула и смена точки коммитятся вместе: потерянный ответ не
        # должен увеличить fail_count без изменения location_version.
        mark_point_failed(rnd.gen_latitude, rnd.gen_longitude, commit=False)

    # Исследовательская попытка уже дала проекту полезный сигнал. После неё
    # восстанавливаем раунд из пула, чтобы не заставлять игрока ждать цепочку
    # новых случайных кандидатов. При маленьком пуле генерация остаётся фолбэком.
    previous_source = rnd.location_source or 'legacy'
    lat, lon, _ = choose_round_candidates(
        game.difficulty or 'medium', 1, prefer_pool=True,
        exclude=excluded_coords,
    )[0]
    rnd.gen_latitude = lat
    rnd.gen_longitude = lon
    rnd.actual_latitude = None
    rnd.actual_longitude = None
    if previous_source.endswith('_recovery'):
        rnd.location_source = previous_source
    else:
        rnd.location_source = f'{previous_source}_recovery'[:20]
    rnd.skips = (rnd.skips or 0) + 1
    rnd.started_at = None
    rnd.panorama_lookup_ms = None
    rnd.panorama_ready_ms = None
    rnd.panorama_attempts = None
    rnd.panorama_status = None
    db.session.commit()

    return jsonify(_location_payload(game, rnd))


@app.route('/api/game/set_actual_point', methods=['POST'])
def set_actual_point():
    """Сохранить реальные координаты найденной панорамы"""
    data = request.get_json(silent=True) or {}
    game, rnd, error = _round_from_payload(data)
    if error:
        return error

    coords = parse_coords(data)
    if coords is None:
        return jsonify({'error': 'Некорректные координаты'}), 400
    lat, lon = coords

    # Антифрод: панорама, найденная клиентом, должна быть рядом со сгенерированной
    # сервером точкой. Иначе игнорируем — счёт будет считаться по серверной точке,
    # координаты которой в честном клиенте не видны. Это не даёт выдать свою
    # догадку за ответ.
    drift = haversine_distance(lat, lon, rnd.gen_latitude, rnd.gen_longitude)
    if drift > MAX_ACTUAL_POINT_DRIFT_KM:
        app.logger.warning(
            f'Отклонена actual_point: drift={drift:.2f} км '
            f'(round={rnd.round_number}, game_id={game.id})'
        )
        return jsonify({'success': False, 'reason': 'too_far'}), 200

    rnd.actual_latitude = lat
    rnd.actual_longitude = lon
    db.session.commit()

    # Панорама подтверждена — пополняем пул проверенных точек
    add_verified_point(lat, lon)

    return jsonify({'success': True})


@app.route('/api/game/set_address', methods=['POST'])
def set_round_address():
    """Best-effort сохранение адреса, найденного клиентом после показа результата."""
    data = request.get_json(silent=True) or {}
    game, rnd, error = _round_from_payload(
        data, allow_answered=True, require_active=False
    )
    if error:
        return error
    address = ' '.join(str(data.get('address') or '').strip().split())[:300]
    if not address:
        return jsonify({'error': 'Пустой адрес'}), 400
    rnd.address = address
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/game/panorama_metric', methods=['POST'])
@limiter.limit('120 per minute')
def panorama_metric():
    """Принять безопасные агрегируемые метрики загрузки конкретного раунда."""
    data = request.get_json(silent=True) or {}
    game, rnd, error = _round_from_payload(
        data, allow_answered=True, require_active=False
    )
    if error:
        return error

    def bounded_int(name, maximum):
        try:
            return max(0, min(int(data.get(name)), maximum))
        except (TypeError, ValueError):
            return None

    status = str(data.get('status') or '')[:24]
    allowed_statuses = {'ready', 'no_coverage', 'network_error', 'unsupported',
                        'api_error', 'cancelled'}
    rnd.panorama_lookup_ms = bounded_int('lookup_ms', 120_000)
    rnd.panorama_ready_ms = bounded_int('ready_ms', 180_000)
    rnd.panorama_attempts = bounded_int('attempts', 20)
    rnd.panorama_status = status if status in allowed_statuses else None
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/game/guess', methods=['POST'])
def submit_guess():
    """Отправить угаданные координаты (или таймаут раунда без догадки)."""
    data = request.get_json(silent=True) or {}
    game, rnd, error = _round_from_payload(data, allow_answered=True)
    if error:
        return error
    if rnd.answered_at is not None:
        # POST мог успешно сохраниться, а ответ — потеряться в сети. С round_id
        # повтор безопасен и возвращает тот же результат, не двигая игру снова.
        percentile = difficulty_percentile(*_scoring_point(rnd))
        return jsonify(_round_result_payload(
            game, rnd, replayed=True, percentile=percentile
        ))

    coords = parse_coords(data)
    # Клиент может завершить раунд без догадки, когда время вышло
    client_timed_out = isinstance(data, dict) and bool(data.get('timed_out'))
    if coords is None and not client_timed_out:
        return jsonify({'error': 'Некорректные координаты'}), 400

    try:
        # Точные координаты открытой съёмки приходят вместе с догадкой. Это
        # убирает гонку между отдельным best-effort /set_actual_point и /guess:
        # результат всегда считается по той панораме, которую видел игрок.
        panorama_coords = parse_coords({
            'latitude': data.get('panorama_latitude'),
            'longitude': data.get('panorama_longitude'),
        })
        if panorama_coords is not None:
            pano_lat, pano_lon = panorama_coords
            drift = haversine_distance(
                pano_lat, pano_lon, rnd.gen_latitude, rnd.gen_longitude
            )
            if drift <= MAX_ACTUAL_POINT_DRIFT_KM:
                rnd.actual_latitude = pano_lat
                rnd.actual_longitude = pano_lon
            else:
                app.logger.warning(
                    'Отклонена panorama point в /guess: drift=%.2f км '
                    '(round=%s, game_id=%s)',
                    drift, rnd.round_number, game.id,
                )

        actual_lat, actual_lon = _scoring_point(rnd)

        # Совместимость со вкладкой, открытой до обновления фронтенда: новый
        # клиент всегда вызывает /ready, старому не даём застрять навсегда.
        if game.time_limit and rnd.started_at is None:
            rnd.started_at = utcnow()

        # Серверная проверка таймера: опоздавший ответ получает 0 очков
        # (запас — на сетевые задержки и загрузку панорамы).
        forced_timeout = False
        if game.time_limit and rnd.started_at is not None:
            elapsed = (utcnow() - _aware_utc(rnd.started_at)).total_seconds()
            if elapsed > game.time_limit + TIME_LIMIT_GRACE_SECONDS:
                forced_timeout = True

        if coords is None:
            guess_lat = guess_lon = None
            distance = None
            score = 0
        else:
            guess_lat, guess_lon = coords
            distance = haversine_distance(guess_lat, guess_lon, actual_lat, actual_lon)
            score = 0 if forced_timeout else calculate_score(distance)

        timed_out = client_timed_out or forced_timeout

        # Фактическая сложность места по накопленной статистике промахов
        # (None, пока раундов с этой точкой сыграно мало)
        percentile = difficulty_percentile(actual_lat, actual_lon)

        rnd.guess_latitude = guess_lat
        rnd.guess_longitude = guess_lon
        rnd.distance_km = distance
        rnd.score = score
        rnd.timed_out = timed_out
        rnd.answered_at = utcnow()

        game.total_score = (game.total_score or 0) + score
        game.rounds_played = (game.rounds_played or 0) + 1
        game.current_round = (game.current_round or 0) + 1

        is_game_over = game.current_round >= ROUNDS_PER_GAME
        if is_game_over:
            game.completed_at = utcnow()

        db.session.commit()
        # Пополнение молодого пула остаётся для каждого сыгранного раунда,
        # но больше не требует отдельного клиентского POST в критической гонке.
        if rnd.actual_latitude is not None:
            add_verified_point(rnd.actual_latitude, rnd.actual_longitude)
    except Exception:
        db.session.rollback()
        app.logger.exception('Ошибка при обработке догадки')
        return jsonify({'error': 'Ошибка сервера'}), 500

    return jsonify(_round_result_payload(game, rnd, percentile=percentile))


@app.route('/api/game/results', methods=['GET'])
def get_results():
    """Результаты текущей игры: раунды с координатами (для итоговой карты),
    челлендж-сравнение и токен для ссылки-вызова."""
    game = _current_game()
    if game is None:
        return jsonify({'error': 'Игра не начата'}), 400

    rounds = (GameRound.query
              .filter(GameRound.session_id == game.id,
                      GameRound.answered_at.isnot(None))
              .order_by(GameRound.round_number)
              .all())
    rounds_data = [{
        'round': r.round_number,
        'address': r.address,
        'distance_m': int(r.distance_km * 1000) if r.distance_km is not None else None,
        'score': r.score,
        'timed_out': bool(r.timed_out),
        'guess': {'latitude': r.guess_latitude, 'longitude': r.guess_longitude}
                 if r.guess_latitude is not None else None,
        'actual': {'latitude': r.actual_latitude, 'longitude': r.actual_longitude}
                  if r.actual_latitude is not None
                  else {'latitude': r.gen_latitude, 'longitude': r.gen_longitude},
    } for r in rounds]

    payload = {
        'player_name': game.player_name,
        'total_score': game.total_score,
        'max_possible_score': ROUNDS_PER_GAME * MAX_SCORE_PER_ROUND,
        'rounds_played': game.rounds_played,
        'difficulty': game.difficulty,
        'difficulty_name': difficulty_name(game.difficulty),
        'time_limit': game.time_limit,
        'no_move': bool(game.no_move),
        'challenge_token': game.challenge_token if game.completed_at else None,
        'daily': game.daily_date is not None,
        'rounds': rounds_data
    }

    # Сравнение с автором челленджа, если игра начата по ссылке-вызову
    if game.challenged_from_id:
        source = db.session.get(GameSession, game.challenged_from_id)
        if source is not None:
            payload['challenge'] = {
                'opponent_name': source.player_name,
                'opponent_score': source.total_score,
                'your_score': game.total_score,
            }

    return jsonify(payload)


# --------------------------------------------------------------------------
# Ежедневный вызов
# --------------------------------------------------------------------------

@app.route('/api/daily', methods=['GET'])
@limiter.limit('30 per minute')
def daily_info():
    """Информация о вызове дня для стартового экрана.

    Набор точек дня НЕ создаётся здесь (только при старте игры), чтобы
    простое открытие страницы не плодило наборы.
    """
    date = today_msk()
    players = (GameSession.query
               .filter(GameSession.daily_date == date,
                       GameSession.completed_at.isnot(None))
               .count())

    payload = {'date': date.isoformat(), 'players_today': players,
               'played': False, 'your_score': None}

    previous = session.get('daily_game')
    if previous and previous.get('date') == date.isoformat():
        prev_game = db.session.get(GameSession, previous.get('game_id'))
        if prev_game is not None and prev_game.completed_at is not None:
            payload['played'] = True
            payload['your_score'] = prev_game.total_score

    return jsonify(payload)


@app.route('/api/daily/leaderboard', methods=['GET'])
@limiter.limit('30 per minute')
def daily_leaderboard():
    """Топ сегодняшнего вызова дня."""
    date = today_msk()
    top = (GameSession.query
           .filter(GameSession.daily_date == date,
                   GameSession.completed_at.isnot(None))
           .order_by(GameSession.total_score.desc())
           .limit(10)
           .all())
    return jsonify({
        'date': date.isoformat(),
        'leaderboard': [
            {'rank': i + 1, 'player_name': g.player_name, 'total_score': g.total_score}
            for i, g in enumerate(top)
        ]
    })


@app.route('/api/challenge/<token>', methods=['GET'])
@limiter.limit('30 per minute')
def challenge_info(token):
    """Информация о челлендже для стартового экрана приглашённого игрока."""
    source = GameSession.query.filter(
        GameSession.challenge_token == token,
        GameSession.completed_at.isnot(None),
    ).first()
    if source is None:
        return jsonify({'error': 'Челлендж не найден'}), 404

    return jsonify({
        'player_name': source.player_name,
        'total_score': source.total_score,
        'difficulty': source.difficulty,
        'difficulty_name': difficulty_name(source.difficulty),
        'time_limit': source.time_limit,
        'no_move': bool(source.no_move),
        'total_rounds': ROUNDS_PER_GAME,
    })


@app.route('/api/player/stats', methods=['GET'])
@limiter.limit('30 per minute')
def player_stats():
    """Статистика игрока по имени: сколько игр, лучший и средний счёт."""
    name = (request.args.get('name') or '').strip()[:50]
    if not name:
        return jsonify({'error': 'Не указано имя'}), 400

    games, best, avg = (db.session.query(
                db.func.count(GameSession.id),
                db.func.max(GameSession.total_score),
                db.func.avg(GameSession.total_score))
           .filter(GameSession.player_name == name,
                   GameSession.completed_at.isnot(None))
           .one())
    return jsonify({
        'player_name': name,
        'games': int(games or 0),
        'best_score': int(best) if best is not None else None,
        'avg_score': int(round(avg)) if avg is not None else None,
    })


# Периоды таблицы лидеров: сколько последних дней учитывать (None — всё время).
# Вечный топ быстро закостеневает — новичкам нужно за что-то бороться.
LEADERBOARD_PERIODS = {'week': 7, 'month': 30, 'all': None}
LEADERBOARD_LIMIT = 50


@app.route('/api/leaderboard', methods=['GET'])
@limiter.limit('30 per minute')
def get_leaderboard():
    """Получить таблицу лидеров.

    Параметры (необязательные):
      ?difficulty=center|medium|hard — фильтр по режиму (режимы не сравнимы
        напрямую, поэтому без фильтра в каждой строке отдаётся режим);
      ?period=week|month|all — за какой срок (по умолчанию всё время).
    """
    query = GameSession.query.filter(GameSession.completed_at.isnot(None))

    difficulty = request.args.get('difficulty')
    if difficulty in DIFFICULTY_SETTINGS:
        query = query.filter(GameSession.difficulty == difficulty)

    period = request.args.get('period')
    if period not in LEADERBOARD_PERIODS:
        period = 'all'
    days = LEADERBOARD_PERIODS[period]
    if days is not None:
        # completed_at в БД naive UTC — сравниваем с naive UTC
        cutoff = utcnow().replace(tzinfo=None) - timedelta(days=days)
        query = query.filter(GameSession.completed_at >= cutoff)

    top_games = query.order_by(GameSession.total_score.desc()).limit(LEADERBOARD_LIMIT).all()

    return jsonify({
        'difficulty': difficulty if difficulty in DIFFICULTY_SETTINGS else 'all',
        'period': period,
        'leaderboard': [
            {
                'rank': i + 1,
                'player_name': game.player_name,
                'total_score': game.total_score,
                'difficulty': game.difficulty,
                'difficulty_name': difficulty_name(game.difficulty),
                'time_limit': game.time_limit,
                'no_move': bool(game.no_move),
                'date': game.completed_at.strftime('%d.%m.%Y') if game.completed_at else None
            }
            for i, game in enumerate(top_games)
        ]
    })


# --------------------------------------------------------------------------
# Модерация пула точек (владелец)
# --------------------------------------------------------------------------

def _check_admin():
    """Ответ-ошибка, если админ-доступ не разрешён; None — доступ есть.

    Без ADMIN_KEY в окружении админка полностью выключена (404, а не 403 —
    не раскрываем само её существование).
    """
    if not ADMIN_KEY:
        return jsonify({'error': 'Не найдено'}), 404
    provided = request.headers.get('X-Admin-Key', '')
    if not hmac.compare_digest(provided, ADMIN_KEY):
        return jsonify({'error': 'Неверный ключ'}), 403
    return None


@app.route('/admin')
def admin_page():
    """Страница модерации пула точек (карта всех проверенных панорам)."""
    if not ADMIN_KEY:
        return 'Не найдено', 404
    return render_template('admin.html', yandex_api_key=YANDEX_MAPS_API_KEY)


@app.route('/api/admin/points', methods=['GET'])
@limiter.limit('60 per minute')
def admin_list_points():
    """Все точки пула — для карты модерации."""
    error = _check_admin()
    if error:
        return error
    points = VerifiedPoint.query.order_by(VerifiedPoint.created_at.desc()).all()
    return jsonify({'points': [
        {
            'id': p.id,
            'latitude': p.latitude,
            'longitude': p.longitude,
            'dist_from_center_km': round(p.dist_from_center_km, 2),
            'fail_count': p.fail_count or 0,
            'created_at': p.created_at.strftime('%d.%m.%Y') if p.created_at else None,
        }
        for p in points
    ]})


@app.route('/api/admin/stats', methods=['GET'])
@limiter.limit('60 per minute')
def admin_stats():
    """Сводка для владельца: сколько играют и доигрывают ли до конца."""
    error = _check_admin()
    if error:
        return error

    now = utcnow().replace(tzinfo=None)
    week_ago = now - timedelta(days=7)

    total = GameSession.query.count()
    completed_total = GameSession.query.filter(GameSession.completed_at.isnot(None)).count()
    started_7d = GameSession.query.filter(GameSession.created_at >= week_ago).count()
    completed_7d = (GameSession.query
                    .filter(GameSession.completed_at.isnot(None),
                            GameSession.completed_at >= week_ago)
                    .count())
    daily_today = (GameSession.query
                   .filter(GameSession.daily_date == today_msk(),
                           GameSession.completed_at.isnot(None))
                   .count())

    metric_rounds = (GameRound.query
                     .filter(GameRound.panorama_status.isnot(None))
                     .order_by(GameRound.id.desc())
                     .limit(1000)
                     .all())
    ready_times = sorted(
        r.panorama_ready_ms for r in metric_rounds
        if r.panorama_status == 'ready' and r.panorama_ready_ms is not None
    )

    def percentile(values, fraction):
        if not values:
            return None
        index = min(len(values) - 1, int(round((len(values) - 1) * fraction)))
        return values[index]

    source_counts = dict(
        db.session.query(GameRound.location_source, db.func.count(GameRound.id))
        .filter(GameRound.started_at.isnot(None))
        .group_by(GameRound.location_source)
        .all()
    )
    failed_metrics = sum(
        1 for r in metric_rounds
        if r.panorama_status not in ('ready', 'cancelled')
    )

    return jsonify({
        'games_total': total,
        'games_completed_total': completed_total,
        'games_started_7d': started_7d,
        'games_completed_7d': completed_7d,
        'completion_rate_7d': round(completed_7d / started_7d, 2) if started_7d else None,
        'daily_players_today': daily_today,
        'pool_points': VerifiedPoint.query.count(),
        'panorama_samples': len(metric_rounds),
        'panorama_ready_p50_ms': percentile(ready_times, 0.50),
        'panorama_ready_p95_ms': percentile(ready_times, 0.95),
        'panorama_failure_rate': (
            round(failed_metrics / len(metric_rounds), 3) if metric_rounds else None
        ),
        'round_sources': {str(key or 'legacy'): value
                          for key, value in source_counts.items()},
    })


@app.route('/api/admin/points/<int:point_id>', methods=['DELETE'])
@limiter.limit('60 per minute')
def admin_delete_point(point_id):
    """Удалить точку из пула (внутри здания, некрасивое место и т.п.)."""
    error = _check_admin()
    if error:
        return error
    point = db.session.get(VerifiedPoint, point_id)
    if point is None:
        return jsonify({'error': 'Точка не найдена'}), 404
    db.session.delete(point)
    db.session.commit()
    app.logger.info('Модерация: удалена точка пула #%d (%.5f, %.5f)',
                    point_id, point.latitude, point.longitude)
    return jsonify({'success': True})


if __name__ == '__main__':
    # debug управляется переменной окружения, чтобы случайно не запустить
    # интерактивный отладчик в проде. Для локальной разработки: FLASK_DEBUG=true
    app.run(debug=_env_bool('FLASK_DEBUG', False), port=int(os.environ.get('PORT', 5000)))
