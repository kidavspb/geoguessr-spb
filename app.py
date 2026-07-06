import os
import json
import math
import random
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache

from flask import Flask, render_template, jsonify, request, session, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from flask_migrate import Migrate, upgrade as _alembic_upgrade, stamp as _alembic_stamp
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from models import db, Location, GameSession, GameRound, VerifiedPoint, utcnow

# Загружаем переменные окружения из .env
load_dotenv()


def _env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
# URI базы можно переопределить через DATABASE_URL (12-factor): удобно для
# тестов (отдельная/временная БД) и для смены СУБД, не трогая код.
# По умолчанию — прежний sqlite-файл в папке instance/.
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///geoguessr_spb.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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

# API ключ Яндекс Карт из переменной окружения
YANDEX_MAPS_API_KEY = os.environ.get('YANDEX_MAPS_API_KEY', '')
# Ключ HTTP-геокодера Яндекса (отдельный сервис; если не задан — пробуем ключ карт,
# если и его нет — обратное геокодирование просто отключено).
YANDEX_GEOCODER_API_KEY = os.environ.get('YANDEX_GEOCODER_API_KEY',
                                         os.environ.get('YANDEX_MAPS_API_KEY', ''))

db.init_app(app)
migrate = Migrate(app, db, directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'migrations'))

# Rate limiting: in-memory (на процесс). Защищает от забивания лидерборда
# мусорными сессиями и от скриптового перебора, а не от распределённых атак.
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri='memory://',
    default_limits=[],
    enabled=_env_bool('RATELIMIT_ENABLED', True),
)

# Ревизия, соответствующая схеме, которую раньше создавал db.create_all()
# (+ колонка difficulty из старого ручного _ensure_schema).
BASELINE_REVISION = '5f7875fb0c81'


def _run_migrations():
    """Привести БД к актуальной схеме через Alembic.

    Три сценария:
      * пустая БД — миграции создают всё с нуля;
      * БД времён db.create_all() (без alembic_version) — помечаем её нулевой
        ревизией и накатываем остальное;
      * БД уже под Alembic — обычный upgrade (no-op, если она актуальна).
    """
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    if 'game_sessions' in tables and 'alembic_version' not in tables:
        # Совсем старые БД могли не иметь колонки difficulty (её дописывал
        # вручную старый _ensure_schema) — дотягиваем до baseline-схемы.
        columns = {col['name'] for col in inspector.get_columns('game_sessions')}
        if 'difficulty' not in columns:
            db.session.execute(text(
                "ALTER TABLE game_sessions ADD COLUMN difficulty VARCHAR(10) DEFAULT 'medium'"
            ))
            db.session.commit()
        _alembic_stamp(revision=BASELINE_REVISION)
        app.logger.info('БД без Alembic: помечена ревизией %s', BASELINE_REVISION)
    _alembic_upgrade()


# Автоматически догоняем схему при старте. При multi-worker запуске gunicorn
# используйте --preload либо выключите (AUTO_MIGRATE=false) и запускайте
# `flask db upgrade` отдельным шагом деплоя.
if _env_bool('AUTO_MIGRATE', True):
    with app.app_context():
        _run_migrations()

# Константы игры
ROUNDS_PER_GAME = 5
MAX_SCORE_PER_ROUND = 5000
# Максимальное расстояние для СПб (примерно 30 км диаметр города)
MAX_DISTANCE_KM = 30

# Серверный предел перегенераций точки на раунд (клиент сдаётся после 8):
# без него можно бесконечно рероллить точку, пока не выпадет знакомое место.
MAX_SKIPS_PER_ROUND = 10

# Запас к лимиту времени на сетевые задержки и загрузку панорамы.
TIME_LIMIT_GRACE_SECONDS = 20
# Допустимые границы лимита времени на раунд (секунды).
TIME_LIMIT_MIN, TIME_LIMIT_MAX = 30, 600

# Пул проверенных точек: начинаем использовать, когда точек достаточно,
# и оставляем долю свежесгенерированных, чтобы пул продолжал расти.
POOL_MIN_SIZE = 15
POOL_USE_PROBABILITY = 0.7
# Максимальное расстояние точки пула от центра для режима сложности (км);
# None — без ограничения (весь город).
POOL_RADIUS_KM = {'center': 3.0, 'medium': 6.5, 'hard': None}

# Границы Санкт-Петербурга для генерации случайных точек
# Центральная часть города где гарантированно есть панорамы
SPB_BOUNDS = {
    'lat_min': 59.87,
    'lat_max': 60.02,
    'lon_min': 30.15,
    'lon_max': 30.50
}

# Центр СПб (Дворцовая площадь)
SPB_CENTER = (59.939, 30.315)

# Настройки для разных режимов сложности
DIFFICULTY_SETTINGS = {
    'center': {
        'name': 'Центр',
        'description': 'Исторический центр города',
        'center': SPB_CENTER,
        'std_lat': 0.015,  # ~1.5 км разброс
        'std_lon': 0.025,
    },
    'medium': {
        'name': 'Средняя',
        'description': 'Центр и ближайшие районы',
        'center': SPB_CENTER,
        'std_lat': 0.03,   # ~3 км разброс
        'std_lon': 0.05,
    },
    'hard': {
        'name': 'Сложная',
        'description': 'Весь город',
        'center': SPB_CENTER,
        'std_lat': 0.06,   # ~6 км разброс
        'std_lon': 0.12,
    }
}


def difficulty_name(difficulty):
    """Человекочитаемое название режима сложности (для UI/ответов API)."""
    settings = DIFFICULTY_SETTINGS.get(difficulty)
    return settings['name'] if settings else difficulty


def generate_random_point(difficulty='medium'):
    """Генерация случайной точки с bias к центру СПб"""
    settings = DIFFICULTY_SETTINGS.get(difficulty, DIFFICULTY_SETTINGS['medium'])

    # Генерируем точку с нормальным распределением вокруг центра
    while True:
        lat = random.gauss(settings['center'][0], settings['std_lat'])
        lon = random.gauss(settings['center'][1], settings['std_lon'])

        # Проверяем, что точка в пределах города
        if (SPB_BOUNDS['lat_min'] <= lat <= SPB_BOUNDS['lat_max'] and
            SPB_BOUNDS['lon_min'] <= lon <= SPB_BOUNDS['lon_max']):
            return round(lat, 6), round(lon, 6)


def choose_round_points(difficulty, count):
    """Точки для раундов игры: смесь пула проверенных панорам и новых случайных.

    Пока пул мал — все точки случайные (как раньше). Когда точек достаточно,
    большинство раундов стартует с проверенной точки (панорама точно есть,
    без перебора), но часть по-прежнему генерируется — так пул растёт.
    """
    query = VerifiedPoint.query
    radius = POOL_RADIUS_KM.get(difficulty)
    if radius is not None:
        query = query.filter(VerifiedPoint.dist_from_center_km <= radius)

    pool = []
    if query.count() >= POOL_MIN_SIZE:
        pool = query.order_by(db.func.random()).limit(count).all()

    points = []
    pool_iter = iter(pool)
    for _ in range(count):
        picked = None
        if random.random() < POOL_USE_PROBABILITY:
            picked = next(pool_iter, None)
        if picked is not None:
            points.append((picked.latitude, picked.longitude))
        else:
            points.append(generate_random_point(difficulty))
    return points


def add_verified_point(lat, lon):
    """Добавить панораму в пул проверенных точек (с дедупликацией ~10 м)."""
    if not (SPB_BOUNDS['lat_min'] - 0.01 <= lat <= SPB_BOUNDS['lat_max'] + 0.01 and
            SPB_BOUNDS['lon_min'] - 0.02 <= lon <= SPB_BOUNDS['lon_max'] + 0.02):
        return
    lat_key, lon_key = int(round(lat * 10000)), int(round(lon * 10000))
    if VerifiedPoint.query.filter_by(lat_key=lat_key, lon_key=lon_key).first():
        return
    try:
        db.session.add(VerifiedPoint(
            latitude=lat,
            longitude=lon,
            lat_key=lat_key,
            lon_key=lon_key,
            dist_from_center_km=haversine_distance(lat, lon, *SPB_CENTER),
        ))
        db.session.commit()
    except Exception:
        # Гонка на unique-ключе или временная блокировка SQLite — точка пула
        # не критична, просто пропускаем.
        db.session.rollback()


def haversine_distance(lat1, lon1, lat2, lon2):
    """Вычисление расстояния между двумя точками по формуле гаверсинуса"""
    R = 6371  # Радиус Земли в км

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = math.sin(delta_lat / 2) ** 2 + \
        math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def calculate_score(distance_km):
    """Расчёт очков на основе расстояния"""
    if distance_km <= 0.05:  # 50 метров — идеально
        return MAX_SCORE_PER_ROUND
    elif distance_km >= MAX_DISTANCE_KM:
        return 0
    else:
        # Экспоненциальное уменьшение очков
        score = MAX_SCORE_PER_ROUND * math.exp(-distance_km / 3)
        return max(0, int(score))


# Максимальное допустимое расхождение (км) между сгенерированной сервером точкой
# и «реальной» точкой панорамы, которую сообщает клиент. Защита от накрутки очков:
# клиент не может выдать произвольные координаты за правильный ответ.
MAX_ACTUAL_POINT_DRIFT_KM = 1.0


def parse_coords(data):
    """Достать и провалидировать широту/долготу из тела запроса.

    Возвращает (lat, lon) при успехе или None при некорректных данных.
    """
    if not isinstance(data, dict):
        return None
    try:
        lat = float(data['latitude'])
        lon = float(data['longitude'])
    except (KeyError, TypeError, ValueError):
        return None
    if math.isnan(lat) or math.isnan(lon):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def parse_time_limit(value):
    """Провалидировать лимит времени на раунд. None — играем без лимита."""
    if value in (None, '', 0, '0'):
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if TIME_LIMIT_MIN <= seconds <= TIME_LIMIT_MAX:
        return seconds
    return None


def _aware_utc(dt):
    """SQLite возвращает naive datetime — считаем, что это UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@lru_cache(maxsize=1024)
def _reverse_geocode_cached(lat_r, lon_r):
    """Запрос к HTTP-геокодеру Яндекса. Координаты уже округлены (ключ кэша)."""
    params = urllib.parse.urlencode({
        'apikey': YANDEX_GEOCODER_API_KEY,
        'geocode': f'{lon_r},{lat_r}',
        'sco': 'longlat',
        'kind': 'house',
        'format': 'json',
        'results': 1,
        'lang': 'ru_RU',
    })
    url = f'https://geocode-maps.yandex.ru/1.x/?{params}'
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.load(resp)
        members = data['response']['GeoObjectCollection']['featureMember']
        if not members:
            return None
        geo = members[0]['GeoObject']
        # name — «улица, дом»; этого достаточно, город игрок и так знает
        return geo.get('name') or None
    except Exception as e:
        app.logger.debug('Геокодер недоступен: %s', e)
        return None


def reverse_geocode(lat, lon):
    """Адрес точки («улица, дом») или None, если геокодер недоступен/выключен."""
    if not YANDEX_GEOCODER_API_KEY:
        return None
    # Округление до ~10 м: соседние панорамы попадают в один ключ кэша
    return _reverse_geocode_cached(round(lat, 4), round(lon, 4))


def _current_game():
    """Игровая сессия текущего игрока (по game_id из подписанной cookie)."""
    game_id = session.get('game_id')
    if not game_id:
        return None
    return db.session.get(GameSession, game_id)


def _round_row(game):
    """Строка текущего раунда игры (раунды создаются заранее при старте)."""
    if game.current_round is None or game.current_round >= ROUNDS_PER_GAME:
        return None
    return GameRound.query.filter_by(
        session_id=game.id, round_number=game.current_round + 1
    ).first()


def _scoring_point(rnd):
    """Точка, по которой считаются очки: панорама, а если её нет — серверная."""
    if rnd.actual_latitude is not None and rnd.actual_longitude is not None:
        return rnd.actual_latitude, rnd.actual_longitude
    return rnd.gen_latitude, rnd.gen_longitude


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({'error': 'Слишком много запросов. Попробуйте чуть позже.'}), 429


@app.context_processor
def inject_asset_version():
    """Кэш-бастер для CSS/JS: версия — свежайший mtime файлов статики.

    Считается на каждый запрос, чтобы правки статики подхватывались
    браузерами и без перезапуска сервиса.
    """
    version = 0
    for rel in ('css/style.css', 'js/game.js'):
        path = os.path.join(app.static_folder, rel)
        try:
            version = max(version, int(os.path.getmtime(path)))
        except OSError:
            pass
    return {'asset_version': version}


@app.route('/')
def index():
    """Главная страница с игрой"""
    return render_template('index.html', yandex_api_key=YANDEX_MAPS_API_KEY)


@app.route('/favicon.ico')
def favicon():
    """Фавиконка для запросов к корню сайта (браузеры/краулеры идут на /favicon.ico)."""
    return send_from_directory(os.path.join(app.static_folder, 'favicons'), 'favicon.ico',
                               mimetype='image/vnd.microsoft.icon')


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
        challenge_token = (str(data.get('challenge_token') or '')).strip()
        if challenge_token:
            source = GameSession.query.filter(
                GameSession.challenge_token == challenge_token,
                GameSession.completed_at.isnot(None),
            ).first()
            if source is None:
                return jsonify({'error': 'Челлендж не найден или игра ещё не завершена'}), 404
            difficulty = source.difficulty
            time_limit = source.time_limit
        else:
            difficulty = data.get('difficulty', 'medium')  # center, medium, hard
            if difficulty not in DIFFICULTY_SETTINGS:
                difficulty = 'medium'
            time_limit = parse_time_limit(data.get('time_limit'))

        game_session = GameSession(
            player_name=player_name,
            difficulty=difficulty,
            time_limit=time_limit,
            current_round=0,
            challenge_token=secrets.token_urlsafe(12),
            challenged_from_id=source.id if source else None,
        )
        db.session.add(game_session)
        db.session.flush()  # получаем id до создания раундов

        if source is not None:
            # Копируем точки исходной игры: и серверные, и найденные панорамы,
            # чтобы очки обоих игроков считались по одним и тем же местам.
            source_rounds = sorted(source.rounds, key=lambda r: r.round_number)[:ROUNDS_PER_GAME]
            for r in source_rounds:
                db.session.add(GameRound(
                    session_id=game_session.id,
                    round_number=r.round_number,
                    gen_latitude=r.gen_latitude if r.gen_latitude is not None else r.actual_latitude,
                    gen_longitude=r.gen_longitude if r.gen_longitude is not None else r.actual_longitude,
                    actual_latitude=r.actual_latitude,
                    actual_longitude=r.actual_longitude,
                ))
        else:
            for i, (lat, lon) in enumerate(choose_round_points(difficulty, ROUNDS_PER_GAME), start=1):
                db.session.add(GameRound(
                    session_id=game_session.id,
                    round_number=i,
                    gen_latitude=lat,
                    gen_longitude=lon,
                ))

        db.session.commit()

        # В cookie — только id игры; заодно чистим ключи старого формата,
        # когда точки хранились прямо в сессии.
        session['game_id'] = game_session.id
        for legacy_key in ('random_points', 'actual_points', 'current_round', 'difficulty'):
            session.pop(legacy_key, None)

        app.logger.info(f'Игра начата: game_id={game_session.id}, player={player_name}, '
                        f'difficulty={difficulty}, time_limit={time_limit}, '
                        f'challenge={"да" if source else "нет"}')

        response = {
            'game_id': game_session.id,
            'total_rounds': ROUNDS_PER_GAME,
            'difficulty': difficulty,
            'difficulty_name': difficulty_name(difficulty),
            'time_limit': time_limit,
            'message': 'Игра началась!'
        }
        if source is not None:
            response['challenge'] = {
                'opponent_name': source.player_name,
                'opponent_score': source.total_score,
            }
        return jsonify(response)
    except Exception as e:
        app.logger.error(f'Ошибка при старте игры: {str(e)}')
        db.session.rollback()
        return jsonify({'error': 'Ошибка сервера'}), 500


@app.route('/api/game/location', methods=['GET'])
def get_current_location():
    """Получить текущую локацию для угадывания"""
    game = _current_game()
    if game is None:
        return jsonify({'error': 'Игра не начата'}), 400

    if game.completed_at is not None or (game.current_round or 0) >= ROUNDS_PER_GAME:
        return jsonify({'error': 'Игра завершена', 'game_over': True}), 400

    rnd = _round_row(game)
    if rnd is None or rnd.gen_latitude is None:
        return jsonify({'error': 'Игра не начата'}), 400

    # Отметка старта раунда — от неё отсчитывается лимит времени
    if rnd.started_at is None:
        rnd.started_at = utcnow()
        db.session.commit()

    return jsonify({
        'round': (game.current_round or 0) + 1,
        'total_rounds': ROUNDS_PER_GAME,
        'time_limit': game.time_limit,
        # Координаты для поиска панорамы
        'latitude': rnd.gen_latitude,
        'longitude': rnd.gen_longitude
    })


@app.route('/api/game/skip_location', methods=['POST'])
@limiter.limit('60 per minute')
def skip_location():
    """Перегенерировать точку текущего раунда.

    Нужно, когда в сгенерированной точке нет панорамы Яндекса. Количество
    перегенераций ограничено и на сервере: иначе точку можно рероллить,
    пока не выпадет знакомое место.
    """
    game = _current_game()
    if game is None:
        return jsonify({'error': 'Игра не начата'}), 400

    if game.completed_at is not None or (game.current_round or 0) >= ROUNDS_PER_GAME:
        return jsonify({'error': 'Игра завершена', 'game_over': True}), 400

    rnd = _round_row(game)
    if rnd is None:
        return jsonify({'error': 'Игра не начата'}), 400

    if (rnd.skips or 0) >= MAX_SKIPS_PER_ROUND:
        return jsonify({'error': 'Лимит перегенераций точки для этого раунда исчерпан'}), 429

    lat, lon = choose_round_points(game.difficulty or 'medium', 1)[0]
    rnd.gen_latitude = lat
    rnd.gen_longitude = lon
    rnd.actual_latitude = None
    rnd.actual_longitude = None
    rnd.skips = (rnd.skips or 0) + 1
    # Игрок ещё ничего не видел — таймер раунда честно перезапускается
    rnd.started_at = utcnow()
    db.session.commit()

    return jsonify({
        'round': (game.current_round or 0) + 1,
        'total_rounds': ROUNDS_PER_GAME,
        'time_limit': game.time_limit,
        'latitude': lat,
        'longitude': lon
    })


@app.route('/api/game/set_actual_point', methods=['POST'])
def set_actual_point():
    """Сохранить реальные координаты найденной панорамы"""
    game = _current_game()
    if game is None:
        return jsonify({'error': 'Игра не начата'}), 400

    coords = parse_coords(request.get_json(silent=True))
    if coords is None:
        return jsonify({'error': 'Некорректные координаты'}), 400
    lat, lon = coords

    rnd = _round_row(game)
    if rnd is None or rnd.gen_latitude is None:
        return jsonify({'error': 'Раунд недоступен'}), 400

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


@app.route('/api/game/guess', methods=['POST'])
def submit_guess():
    """Отправить угаданные координаты (или таймаут раунда без догадки)."""
    game = _current_game()
    if game is None:
        return jsonify({'error': 'Игра не начата'}), 400

    if game.completed_at is not None or (game.current_round or 0) >= ROUNDS_PER_GAME:
        return jsonify({'error': 'Игра завершена'}), 400

    rnd = _round_row(game)
    if rnd is None or rnd.gen_latitude is None:
        return jsonify({'error': 'Игра не начата'}), 400
    if rnd.answered_at is not None:
        return jsonify({'error': 'Раунд уже сыгран'}), 400

    data = request.get_json(silent=True)
    coords = parse_coords(data)
    # Клиент может завершить раунд без догадки, когда время вышло
    client_timed_out = isinstance(data, dict) and bool(data.get('timed_out'))
    if coords is None and not client_timed_out:
        return jsonify({'error': 'Некорректные координаты'}), 400

    try:
        actual_lat, actual_lon = _scoring_point(rnd)

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

        # Адрес точки ответа — для экрана результата («Это было: …»)
        address = reverse_geocode(actual_lat, actual_lon)

        rnd.guess_latitude = guess_lat
        rnd.guess_longitude = guess_lon
        rnd.distance_km = distance
        rnd.score = score
        rnd.address = address
        rnd.timed_out = timed_out
        rnd.answered_at = utcnow()

        game.total_score = (game.total_score or 0) + score
        game.rounds_played = (game.rounds_played or 0) + 1
        game.current_round = (game.current_round or 0) + 1

        is_game_over = game.current_round >= ROUNDS_PER_GAME
        if is_game_over:
            game.completed_at = utcnow()

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Ошибка при обработке догадки: {str(e)}')
        return jsonify({'error': 'Ошибка сервера'}), 500

    return jsonify({
        'correct_location': {
            'name': address or f'Точка раунда {rnd.round_number}',
            'description': 'Случайное место в Санкт-Петербурге',
            'latitude': actual_lat,
            'longitude': actual_lon
        },
        'guess': {
            'latitude': guess_lat,
            'longitude': guess_lon
        } if guess_lat is not None else None,
        'address': address,
        'timed_out': timed_out,
        'distance_km': round(distance, 2) if distance is not None else None,
        'distance_m': int(distance * 1000) if distance is not None else None,
        'score': score,
        'total_score': game.total_score,
        'round': rnd.round_number,
        'is_game_over': is_game_over
    })


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
    rounds_data = []
    for r in rounds:
        rounds_data.append({
            'round': r.round_number,
            'location_name': r.address or f'Раунд {r.round_number}',
            'address': r.address,
            'distance_m': int(r.distance_km * 1000) if r.distance_km is not None else None,
            'score': r.score,
            'timed_out': bool(r.timed_out),
            'guess': {'latitude': r.guess_latitude, 'longitude': r.guess_longitude}
                     if r.guess_latitude is not None else None,
            'actual': {'latitude': r.actual_latitude, 'longitude': r.actual_longitude}
                      if r.actual_latitude is not None
                      else {'latitude': r.gen_latitude, 'longitude': r.gen_longitude},
        })

    payload = {
        'player_name': game.player_name,
        'total_score': game.total_score,
        'max_possible_score': ROUNDS_PER_GAME * MAX_SCORE_PER_ROUND,
        'rounds_played': game.rounds_played,
        'difficulty': game.difficulty,
        'difficulty_name': difficulty_name(game.difficulty),
        'time_limit': game.time_limit,
        'challenge_token': game.challenge_token if game.completed_at else None,
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
        'total_rounds': ROUNDS_PER_GAME,
    })


@app.route('/api/player/stats', methods=['GET'])
@limiter.limit('30 per minute')
def player_stats():
    """Статистика игрока по имени: сколько игр, лучший и средний счёт."""
    name = (request.args.get('name') or '').strip()[:50]
    if not name:
        return jsonify({'error': 'Не указано имя'}), 400

    row = (db.session.query(
                db.func.count(GameSession.id),
                db.func.max(GameSession.total_score),
                db.func.avg(GameSession.total_score))
           .filter(GameSession.player_name == name,
                   GameSession.completed_at.isnot(None))
           .one())
    games, best, avg = row
    return jsonify({
        'player_name': name,
        'games': int(games or 0),
        'best_score': int(best) if best is not None else None,
        'avg_score': int(round(avg)) if avg is not None else None,
    })


@app.route('/api/leaderboard', methods=['GET'])
@limiter.limit('30 per minute')
def get_leaderboard():
    """Получить таблицу лидеров.

    Необязательный параметр ?difficulty=center|medium|hard фильтрует таблицу
    по режиму сложности — разные режимы не сравнимы напрямую, поэтому без
    фильтра показываем все, но в каждой строке отдаём режим для контекста.
    """
    query = GameSession.query.filter(GameSession.completed_at.isnot(None))

    difficulty = request.args.get('difficulty')
    if difficulty in DIFFICULTY_SETTINGS:
        query = query.filter(GameSession.difficulty == difficulty)

    top_games = query.order_by(GameSession.total_score.desc()).limit(10).all()

    return jsonify({
        'difficulty': difficulty if difficulty in DIFFICULTY_SETTINGS else 'all',
        'leaderboard': [
            {
                'rank': i + 1,
                'player_name': game.player_name,
                'total_score': game.total_score,
                'difficulty': game.difficulty,
                'difficulty_name': difficulty_name(game.difficulty),
                'time_limit': game.time_limit,
                'date': game.completed_at.strftime('%d.%m.%Y') if game.completed_at else None
            }
            for i, game in enumerate(top_games)
        ]
    })


@app.route('/api/locations/count', methods=['GET'])
def get_locations_count():
    """Получить количество локаций в базе"""
    count = Location.query.count()
    return jsonify({'count': count})


if __name__ == '__main__':
    # debug управляется переменной окружения, чтобы случайно не запустить
    # интерактивный отладчик в проде. Для локальной разработки: FLASK_DEBUG=true
    app.run(debug=_env_bool('FLASK_DEBUG', False), port=int(os.environ.get('PORT', 5000)))
