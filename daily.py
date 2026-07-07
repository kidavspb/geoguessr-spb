"""Ежедневный вызов: один общий набор точек на день для всех игроков.

День считается по петербургскому времени (МСК, UTC+3). Набор создаётся
лениво при первом обращении; гонка двух воркеров разрешается unique-индексом
по дате.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from models import db, DailyChallenge
from game_logic import ROUNDS_PER_GAME
from pool import choose_round_points

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))

# Ежедневный вызов играется в фиксированном режиме, чтобы результаты
# всех игроков дня были сравнимы.
DAILY_DIFFICULTY = 'medium'


def today_msk():
    """Текущая дата по Петербургу — граница дня для ежедневного вызова."""
    return datetime.now(MSK).date()


def get_or_create_daily():
    """Набор точек сегодняшнего дня (создаётся при первом обращении)."""
    date = today_msk()
    challenge = DailyChallenge.query.filter_by(date=date).first()
    if challenge is not None:
        return challenge

    points = choose_round_points(DAILY_DIFFICULTY, ROUNDS_PER_GAME)
    challenge = DailyChallenge(date=date, points_json=json.dumps(points))
    try:
        db.session.add(challenge)
        db.session.commit()
    except Exception:
        # Гонка воркеров на unique(date): набор уже создал кто-то другой
        db.session.rollback()
        challenge = DailyChallenge.query.filter_by(date=date).first()
        if challenge is None:
            raise
    return challenge


def daily_points(challenge):
    """Точки раундов вызова: [(lat, lon), ...]."""
    return [tuple(p) for p in json.loads(challenge.points_json)]
