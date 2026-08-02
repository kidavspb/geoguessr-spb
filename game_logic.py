"""Чистая игровая логика: константы, расчёты, валидация.

Модуль не зависит от Flask и БД — всё тестируется напрямую.
"""
import math
import random

# Константы игры
ROUNDS_PER_GAME = 5
MAX_SCORE_PER_ROUND = 5000
# Максимальное расстояние для СПб (примерно 30 км диаметр города)
MAX_DISTANCE_KM = 30

# Серверный предел перегенераций точки на раунд (клиент сдаётся после 8):
# без него можно бесконечно рероллить точку, пока не выпадет знакомое место.
MAX_SKIPS_PER_ROUND = 10

# Максимальное допустимое расхождение (км) между сгенерированной сервером точкой
# и «реальной» точкой панорамы, которую сообщает клиент. Защита от накрутки очков:
# клиент не может выдать произвольные координаты за правильный ответ.
MAX_ACTUAL_POINT_DRIFT_KM = 1.0

# Запас к лимиту времени на сетевые задержки и загрузку панорамы.
TIME_LIMIT_GRACE_SECONDS = 20
# Допустимые границы лимита времени на раунд (секунды).
TIME_LIMIT_MIN, TIME_LIMIT_MAX = 30, 600

# Границы Санкт-Петербурга для генерации случайных точек
# (центральная часть города, где в основном есть панорамы)
SPB_BOUNDS = {
    'lat_min': 59.87,
    'lat_max': 60.02,
    'lon_min': 30.15,
    'lon_max': 30.50
}

# Центр СПб (Дворцовая площадь) — вокруг него генерируются точки
SPB_CENTER = (59.939, 30.315)

# Режимы сложности: разброс гауссианы вокруг центра города
DIFFICULTY_SETTINGS = {
    'center': {
        'name': 'Центр',
        'std_lat': 0.015,  # ~1.5 км разброс
        'std_lon': 0.025,
    },
    'medium': {
        'name': 'Средняя',
        'std_lat': 0.03,   # ~3 км разброс
        'std_lon': 0.05,
    },
    'hard': {
        'name': 'Сложная',
        'std_lat': 0.06,   # ~6 км разброс
        'std_lon': 0.12,
    }
}


def difficulty_name(difficulty):
    """Человекочитаемое название режима сложности (для UI/ответов API)."""
    settings = DIFFICULTY_SETTINGS.get(difficulty)
    return settings['name'] if settings else difficulty


def generate_random_point(difficulty='medium'):
    """Случайная точка с нормальным распределением вокруг центра СПб."""
    settings = DIFFICULTY_SETTINGS.get(difficulty, DIFFICULTY_SETTINGS['medium'])

    while True:
        lat = random.gauss(SPB_CENTER[0], settings['std_lat'])
        lon = random.gauss(SPB_CENTER[1], settings['std_lon'])

        # Точка должна попасть в пределы города
        if (SPB_BOUNDS['lat_min'] <= lat <= SPB_BOUNDS['lat_max'] and
                SPB_BOUNDS['lon_min'] <= lon <= SPB_BOUNDS['lon_max']):
            return round(lat, 6), round(lon, 6)


def haversine_distance(lat1, lon1, lat2, lon2):
    """Расстояние между двумя точками (км) по формуле гаверсинуса."""
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
    """Очки за раунд: максимум в пределах 50 м, дальше — экспоненциальный спад."""
    if distance_km <= 0.05:
        return MAX_SCORE_PER_ROUND
    if distance_km >= MAX_DISTANCE_KM:
        return 0
    return max(0, int(MAX_SCORE_PER_ROUND * math.exp(-distance_km / 3)))


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
    if not math.isfinite(lat) or not math.isfinite(lon):
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
