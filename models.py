from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


def utcnow():
    """Текущее время в UTC (timezone-aware).

    Замена устаревшего datetime.utcnow (deprecated в Python 3.12).
    """
    return datetime.now(timezone.utc)


class Location(db.Model):
    """Модель локации Санкт-Петербурга"""
    __tablename__ = 'locations'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    image_url = db.Column(db.String(500), nullable=False)
    difficulty = db.Column(db.Integer, default=1)  # 1-5 сложность
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'image_url': self.image_url,
            'difficulty': self.difficulty
        }


class GameSession(db.Model):
    """Модель игровой сессии.

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
    # Нужен, чтобы таблица лидеров была честной: разные режимы не сравнимы напрямую.
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
    created_at = db.Column(db.DateTime, default=utcnow)
    completed_at = db.Column(db.DateTime)

    rounds = db.relationship('GameRound', backref='session', lazy=True,
                             foreign_keys='GameRound.session_id')

    def to_dict(self):
        return {
            'id': self.id,
            'player_name': self.player_name,
            'total_score': self.total_score,
            'rounds_played': self.rounds_played,
            'difficulty': self.difficulty,
            'time_limit': self.time_limit,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }


class GameRound(db.Model):
    """Модель раунда игры.

    Строки создаются заранее при старте игры (со сгенерированными точками),
    а заполняются по ходу: реальная точка панорамы — когда клиент её нашёл,
    догадка/очки — когда игрок ответил. Раунд считается сыгранным, когда
    заполнено answered_at.
    """
    __tablename__ = 'game_rounds'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('game_sessions.id'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)  # Nullable для случайных точек
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
    created_at = db.Column(db.DateTime, default=utcnow)

    location = db.relationship('Location', backref='rounds')

    def to_dict(self):
        return {
            'id': self.id,
            'session_id': self.session_id,
            'location_id': self.location_id,
            'guess_latitude': self.guess_latitude,
            'guess_longitude': self.guess_longitude,
            'actual_latitude': self.actual_latitude,
            'actual_longitude': self.actual_longitude,
            'distance_km': self.distance_km,
            'score': self.score,
            'round_number': self.round_number,
            'address': self.address,
            'timed_out': bool(self.timed_out)
        }


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
    created_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint('lat_key', 'lon_key', name='uq_verified_points_key'),
    )
