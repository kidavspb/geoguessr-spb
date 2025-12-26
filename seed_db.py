#!/usr/bin/env python3
"""
Скрипт для заполнения базы данных локациями Санкт-Петербурга
"""
import json
import os
from app import app, db
from models import Location


def seed_database():
    """Загрузить локации из JSON в базу данных"""
    
    # Путь к файлу с данными
    data_path = os.path.join(os.path.dirname(__file__), 'data', 'locations.json')
    
    with open(data_path, 'r', encoding='utf-8') as f:
        locations_data = json.load(f)
    
    with app.app_context():
        # Создаём таблицы если их нет
        db.create_all()
        
        # Проверяем, есть ли уже данные
        existing_count = Location.query.count()
        if existing_count > 0:
            print(f"В базе уже есть {existing_count} локаций.")
            response = input("Очистить и загрузить заново? (y/n): ")
            if response.lower() == 'y':
                Location.query.delete()
                db.session.commit()
                print("База очищена.")
            else:
                print("Отмена операции.")
                return
        
        # Добавляем локации
        added = 0
        for loc_data in locations_data:
            location = Location(
                name=loc_data['name'],
                description=loc_data.get('description', ''),
                latitude=loc_data['latitude'],
                longitude=loc_data['longitude'],
                image_url=loc_data['image_url'],
                difficulty=loc_data.get('difficulty', 1)
            )
            db.session.add(location)
            added += 1
            print(f"  + {location.name}")
        
        db.session.commit()
        print(f"\nУспешно добавлено {added} локаций!")
        print("База данных готова к использованию.")


if __name__ == '__main__':
    seed_database()
