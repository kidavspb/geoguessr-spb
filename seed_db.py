#!/usr/bin/env python3
"""
Скрипт для заполнения базы данных локациями Санкт-Петербурга
"""
import json
import os
import sys
from app import app, db
from models import Location


def seed_database(force=False):
    """Загрузить локации из JSON в базу данных.

    force=True — очистить существующие локации без запроса (для CI/деплоя).
    """

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
            should_reset = force
            # Спрашиваем только в интерактивном режиме; в неинтерактивном
            # (деплой, CI) без --force просто выходим, не падая на input().
            if not should_reset and sys.stdin.isatty():
                response = input("Очистить и загрузить заново? (y/n): ")
                should_reset = response.lower() == 'y'
            if should_reset:
                Location.query.delete()
                db.session.commit()
                print("База очищена.")
            else:
                print("Отмена операции (для перезаписи запустите с --force).")
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
    force = '--force' in sys.argv or '--reset' in sys.argv
    seed_database(force=force)
