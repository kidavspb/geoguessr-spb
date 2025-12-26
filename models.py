from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
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
    """Модель игровой сессии"""
    __tablename__ = 'game_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    player_name = db.Column(db.String(100), default='Аноним')
    total_score = db.Column(db.Integer, default=0)
    rounds_played = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    
    rounds = db.relationship('GameRound', backref='session', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'player_name': self.player_name,
            'total_score': self.total_score,
            'rounds_played': self.rounds_played,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }


class GameRound(db.Model):
    """Модель раунда игры"""
    __tablename__ = 'game_rounds'
    
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('game_sessions.id'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)  # Nullable для случайных точек
    guess_latitude = db.Column(db.Float)
    guess_longitude = db.Column(db.Float)
    actual_latitude = db.Column(db.Float)  # Реальные координаты панорамы
    actual_longitude = db.Column(db.Float)
    distance_km = db.Column(db.Float)
    score = db.Column(db.Integer, default=0)
    round_number = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
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
            'round_number': self.round_number
        }
