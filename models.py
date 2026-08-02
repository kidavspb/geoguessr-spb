from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


def utcnow():
    """Текущее время в UTC (timezone-aware).

    Замена устаревшего datetime.utcnow (deprecated в Python 3.12).
    """
    return datetime.now(timezone.utc)


class GameSession(db.Model):
    """Игровая сессия.

    Всё состояние игры (точки раундов, текущий раунд) хранится на сервере:
    в cookie-сессии клиента остаётся только id игры. Cookie Flask подписана,
    но не зашифрована — храни мы точки в ней, игрок мог бы просто декодировать
    base64 и прочитать ответ.
    """
    __tablename__ = 'game_sessions'

    id = db.Column(db.Integer, primary_key=True)
    player_name = db.Column(db.String(100), default='Аноним')
    total_score = db.Column(db.Integer, default=0)
    rounds_played = db.Column(db.Integer, default=0)
    # Режим сложности, в котором сыграна игра (center / medium / hard).
    # Нужен, чтобы таблица лидеров была честной: режимы не сравнимы напрямую.
    difficulty = db.Column(db.String(10), default='medium')
    # Номер текущего раунда (0-based). Раньше жил в cookie-сессии клиента,
    # что позволяло реплеить старую cookie и переигрывать раунды.
    current_round = db.Column(db.Integer, default=0)
    # Лимит времени на раунд в секундах; NULL — без лимита.
    time_limit = db.Column(db.Integer)
    # Токен для челлендж-ссылок: по нему друг может сыграть те же самые точки.
    challenge_token = db.Column(db.String(32), unique=True, index=True)
    # Если игра начата по челлендж-ссылке — id исходной игры для сравнения счёта.
    challenged_from_id = db.Column(db.Integer, db.ForeignKey('game_sessions.id'))
    # Дата ежедневного вызова, если игра сыграна в нём (для лидерборда дня).
    daily_date = db.Column(db.Date, index=True)
    # Режим «без перемещения»: по панораме нельзя ходить, только осматриваться.
    no_move = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    completed_at = db.Column(db.DateTime)

    rounds = db.relationship('GameRound', backref='session', lazy=True,
                             foreign_keys='GameRound.session_id')


class GameRound(db.Model):
    """Раунд игры.

    Строки создаются заранее при старте игры (со сгенерированными точками),
    а заполняются по ходу: реальная точка панорамы — когда клиент её нашёл,
    догадка/очки — когда игрок ответил. Раунд считается сыгранным, когда
    заполнено answered_at.
    """
    __tablename__ = 'game_rounds'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('game_sessions.id'), nullable=False)
    # Сгенерированная сервером точка раунда (клиент ищет панораму рядом с ней)
    gen_latitude = db.Column(db.Float)
    gen_longitude = db.Column(db.Float)
    guess_latitude = db.Column(db.Float)
    guess_longitude = db.Column(db.Float)
    actual_latitude = db.Column(db.Float)  # Реальные координаты панорамы
    actual_longitude = db.Column(db.Float)
    distance_km = db.Column(db.Float)
    score = db.Column(db.Integer, default=0)
    round_number = db.Column(db.Integer, nullable=False)
    # Адрес точки ответа (обратное геокодирование), показывается на экране результата
    address = db.Column(db.String(300))
    # Сколько раз точка раунда перегенерировалась (серверный лимит против абьюза)
    skips = db.Column(db.Integer, default=0)
    # Когда точка выдана клиенту — от этого момента считается лимит времени
    started_at = db.Column(db.DateTime)
    # Когда игрок ответил; NULL — раунд ещё не сыгран
    answered_at = db.Column(db.DateTime)
    # Раунд завершён по истечении времени (без догадки или принудительно)
    timed_out = db.Column(db.Boolean, default=False)
    # Откуда взялась выданная точка: проверенный пул, исследовательская
    # генерация, ежедневный вызов или челлендж. Нужна для контроля качества
    # пула и сравнения скорости разных типов раундов.
    location_source = db.Column(db.String(20))
    # Клиентские метрики загрузки панорамы. Они не участвуют в игровой логике
    # и заполняются best-effort после появления панорамы на экране.
    panorama_lookup_ms = db.Column(db.Integer)
    panorama_ready_ms = db.Column(db.Integer)
    panorama_attempts = db.Column(db.Integer)
    panorama_status = db.Column(db.String(24))
    created_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint('session_id', 'round_number',
                            name='uq_game_round_session_round'),
    )


class VerifiedPoint(db.Model):
    """Пул проверенных точек — мест, где панорама Яндекса точно существует.

    Пополняется автоматически: каждая панорама, найденная клиентом в честной
    игре, попадает сюда. Новые игры берут точки из пула — раунд стартует сразу,
    без перебора случайных точек в поисках панорамы.
    """
    __tablename__ = 'verified_points'

    id = db.Column(db.Integer, primary_key=True)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    # Ключи дедупликации: координаты, округлённые до ~10 м (int(coord * 10000)).
    # Две панорамы ближе ~10 м считаются одной точкой пула.
    lat_key = db.Column(db.Integer, nullable=False)
    lon_key = db.Column(db.Integer, nullable=False)
    # Расстояние до центра города — для отбора точек под режим сложности
    dist_from_center_km = db.Column(db.Float, nullable=False)
    # Сколько раз подряд у точки не нашлась панорама (панорамы иногда
    # пропадают). На пороге точка удаляется из пула; при успешном
    # подтверждении панорамы счётчик сбрасывается.
    fail_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint('lat_key', 'lon_key', name='uq_verified_points_key'),
    )


class DailyChallenge(db.Model):
    """Ежедневный вызов: один общий набор точек на день для всех игроков.

    Создаётся лениво при первом обращении за день; точки берутся из пула
    проверенных панорам (с добивкой случайными, пока пул мал).
    """
    __tablename__ = 'daily_challenges'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, index=True, nullable=False)
    # JSON-список [[lat, lon], ...] — точки раундов дня
    points_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
