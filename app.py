"""GeoGuessr СПб («Петербургский следопыт») — Flask-приложение и API.

Структура проекта:
  game_logic.py — чистая игровая логика (константы, расчёты, валидация)
  pool.py       — пул проверенных точек с панорамами
  geocoder.py   — обратное геокодирование (адрес точки ответа)
  models.py     — модели БД
  app.py        — конфигурация, миграции, rate limiting, HTTP-роуты
"""
import os
import secrets
from datetime import timezone

from flask import Flask, render_template, jsonify, request, session, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from flask_migrate import Migrate, upgrade as _alembic_upgrade, stamp as _alembic_stamp
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

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
    choose_round_points, add_verified_point
from geocoder import reverse_geocode

# Загружаем переменные окружения из .env
load_dotenv()


def _env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
# URI базы можно переопределить через DATABASE_URL (12-factor): удобно для
# тестов (отдельная/временная БД) и для смены СУБД, не трогая код.
# По умолчанию — sqlite-файл в папке instance/.
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

# API ключ Яндекс Карт (для JS API на странице)
YANDEX_MAPS_API_KEY = os.environ.get('YANDEX_MAPS_API_KEY', '')

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


# --------------------------------------------------------------------------
# Страницы
# --------------------------------------------------------------------------

@app.route('/')
def index():
    """Главная страница с игрой"""
    return render_template('index.html', yandex_api_key=YANDEX_MAPS_API_KEY)


@app.route('/favicon.ico')
def favicon():
    """Фавиконка для запросов к корню сайта (браузеры/краулеры идут на /favicon.ico)."""
    return send_from_directory(os.path.join(app.static_folder, 'favicons'), 'favicon.ico',
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

        session['game_id'] = game_session.id

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
    game, rnd, error = _load_active_round()
    if error:
        return error

    # Отметка старта раунда — от неё отсчитывается лимит времени
    if rnd.started_at is None:
        rnd.started_at = utcnow()
        db.session.commit()

    return jsonify({
        'round': rnd.round_number,
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
    game, rnd, error = _load_active_round()
    if error:
        return error

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
        'round': rnd.round_number,
        'total_rounds': ROUNDS_PER_GAME,
        'time_limit': game.time_limit,
        'latitude': lat,
        'longitude': lon
    })


@app.route('/api/game/set_actual_point', methods=['POST'])
def set_actual_point():
    """Сохранить реальные координаты найденной панорамы"""
    game, rnd, error = _load_active_round()
    if error:
        return error

    coords = parse_coords(request.get_json(silent=True))
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


@app.route('/api/game/guess', methods=['POST'])
def submit_guess():
    """Отправить угаданные координаты (или таймаут раунда без догадки)."""
    game, rnd, error = _load_active_round()
    if error:
        return error
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


if __name__ == '__main__':
    # debug управляется переменной окружения, чтобы случайно не запустить
    # интерактивный отладчик в проде. Для локальной разработки: FLASK_DEBUG=true
    app.run(debug=_env_bool('FLASK_DEBUG', False), port=int(os.environ.get('PORT', 5000)))
