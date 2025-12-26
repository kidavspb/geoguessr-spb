import os
import math
import random
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request, session
from dotenv import load_dotenv
from models import db, Location, GameSession, GameRound

# Загружаем переменные окружения из .env
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///geoguessr_spb.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# API ключ Яндекс Карт из переменной окружения
YANDEX_MAPS_API_KEY = os.environ.get('YANDEX_MAPS_API_KEY', '')

db.init_app(app)

# Константы игры
ROUNDS_PER_GAME = 5
MAX_SCORE_PER_ROUND = 5000
# Максимальное расстояние для СПб (примерно 30 км диаметр города)
MAX_DISTANCE_KM = 30

# Границы Санкт-Петербурга для генерации случайных точек
# Центральная часть города где гарантированно есть панорамы
SPB_BOUNDS = {
    'lat_min': 59.87,
    'lat_max': 60.02,
    'lon_min': 30.15,
    'lon_max': 30.50
}


def generate_random_point():
    """Генерация случайной точки в пределах СПб"""
    lat = random.uniform(SPB_BOUNDS['lat_min'], SPB_BOUNDS['lat_max'])
    lon = random.uniform(SPB_BOUNDS['lon_min'], SPB_BOUNDS['lon_max'])
    return round(lat, 6), round(lon, 6)


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


@app.route('/')
def index():
    """Главная страница с игрой"""
    return render_template('index.html', yandex_api_key=YANDEX_MAPS_API_KEY)


@app.route('/api/game/start', methods=['POST'])
def start_game():
    """Начать новую игру"""
    try:
        data = request.get_json() or {}
        player_name = data.get('player_name', 'Аноним')

        # Создаём новую игровую сессию
        game_session = GameSession(player_name=player_name)
        db.session.add(game_session)
        db.session.commit()

        # Генерируем случайные точки для игры
        random_points = [generate_random_point() for _ in range(ROUNDS_PER_GAME)]

        # Сохраняем данные в сессии
        session['game_id'] = game_session.id
        session['random_points'] = random_points  # [(lat, lon), ...]
        session['actual_points'] = [None] * ROUNDS_PER_GAME  # Реальные координаты панорам
        session['current_round'] = 0

        app.logger.info(f'Игра начата: game_id={game_session.id}, player={player_name}')

        return jsonify({
            'game_id': game_session.id,
            'total_rounds': ROUNDS_PER_GAME,
            'message': 'Игра началась!'
        })
    except Exception as e:
        app.logger.error(f'Ошибка при старте игры: {str(e)}')
        db.session.rollback()
        return jsonify({'error': f'Ошибка сервера: {str(e)}'}), 500


@app.route('/api/game/location', methods=['GET'])
def get_current_location():
    """Получить текущую локацию для угадывания"""
    if 'game_id' not in session:
        return jsonify({'error': 'Игра не начата'}), 400
    
    current_round = session.get('current_round', 0)
    random_points = session.get('random_points', [])
    
    if current_round >= len(random_points):
        return jsonify({'error': 'Игра завершена', 'game_over': True}), 400
    
    lat, lon = random_points[current_round]
    
    return jsonify({
        'round': current_round + 1,
        'total_rounds': ROUNDS_PER_GAME,
        # Координаты для поиска панорамы
        'latitude': lat,
        'longitude': lon
    })


@app.route('/api/game/set_actual_point', methods=['POST'])
def set_actual_point():
    """Сохранить реальные координаты найденной панорамы"""
    if 'game_id' not in session:
        return jsonify({'error': 'Игра не начата'}), 400
    
    data = request.get_json()
    if not data or 'latitude' not in data or 'longitude' not in data:
        return jsonify({'error': 'Необходимо указать координаты'}), 400
    
    current_round = session.get('current_round', 0)
    actual_points = session.get('actual_points', [])
    
    if current_round < len(actual_points):
        actual_points[current_round] = (data['latitude'], data['longitude'])
        session['actual_points'] = actual_points
        session.modified = True
    
    return jsonify({'success': True})


@app.route('/api/game/guess', methods=['POST'])
def submit_guess():
    """Отправить угаданные координаты"""
    if 'game_id' not in session:
        return jsonify({'error': 'Игра не начата'}), 400
    
    data = request.get_json()
    if not data or 'latitude' not in data or 'longitude' not in data:
        return jsonify({'error': 'Необходимо указать координаты'}), 400
    
    guess_lat = data['latitude']
    guess_lon = data['longitude']
    
    current_round = session.get('current_round', 0)
    actual_points = session.get('actual_points', [])
    random_points = session.get('random_points', [])
    game_id = session['game_id']
    
    if current_round >= len(random_points):
        return jsonify({'error': 'Игра завершена'}), 400
    
    # Используем реальные координаты панорамы, если они есть
    # Иначе используем изначальные случайные координаты
    if actual_points[current_round]:
        actual_lat, actual_lon = actual_points[current_round]
    else:
        actual_lat, actual_lon = random_points[current_round]
    
    # Вычисляем расстояние и очки
    distance = haversine_distance(
        guess_lat, guess_lon,
        actual_lat, actual_lon
    )
    score = calculate_score(distance)
    
    # Сохраняем раунд в БД (location_id = None для случайных точек)
    game_round = GameRound(
        session_id=game_id,
        location_id=None,
        guess_latitude=guess_lat,
        guess_longitude=guess_lon,
        distance_km=distance,
        score=score,
        round_number=current_round + 1
    )
    db.session.add(game_round)
    
    # Обновляем сессию
    game_session = GameSession.query.get(game_id)
    game_session.total_score += score
    game_session.rounds_played += 1
    
    # Переходим к следующему раунду
    session['current_round'] = current_round + 1
    
    is_game_over = session['current_round'] >= ROUNDS_PER_GAME
    if is_game_over:
        game_session.completed_at = datetime.now(timezone.utc)

    db.session.commit()
    
    return jsonify({
        'correct_location': {
            'name': f'Точка раунда {current_round + 1}',
            'description': f'Случайное место в Санкт-Петербурге',
            'latitude': actual_lat,
            'longitude': actual_lon
        },
        'guess': {
            'latitude': guess_lat,
            'longitude': guess_lon
        },
        'distance_km': round(distance, 2),
        'distance_m': int(distance * 1000),
        'score': score,
        'total_score': game_session.total_score,
        'round': current_round + 1,
        'is_game_over': is_game_over
    })


@app.route('/api/game/results', methods=['GET'])
def get_results():
    """Получить результаты текущей игры"""
    if 'game_id' not in session:
        return jsonify({'error': 'Игра не начата'}), 400
    
    game_session = GameSession.query.get(session['game_id'])
    if not game_session:
        return jsonify({'error': 'Сессия не найдена'}), 404
    
    rounds = GameRound.query.filter_by(session_id=game_session.id).all()
    rounds_data = []
    for r in rounds:
        rounds_data.append({
            'round': r.round_number,
            'location_name': f'Раунд {r.round_number}',
            'distance_m': int(r.distance_km * 1000) if r.distance_km else 0,
            'score': r.score
        })
    
    return jsonify({
        'player_name': game_session.player_name,
        'total_score': game_session.total_score,
        'max_possible_score': ROUNDS_PER_GAME * MAX_SCORE_PER_ROUND,
        'rounds_played': game_session.rounds_played,
        'rounds': rounds_data
    })


@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    """Получить таблицу лидеров"""
    top_games = GameSession.query\
        .filter(GameSession.completed_at.isnot(None))\
        .order_by(GameSession.total_score.desc())\
        .limit(10)\
        .all()
    
    return jsonify({
        'leaderboard': [
            {
                'rank': i + 1,
                'player_name': game.player_name,
                'total_score': game.total_score,
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
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
