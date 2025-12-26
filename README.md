# 🗺️ GeoGuessr СПб

Локальный аналог игры GeoGuessr для Санкт-Петербурга. Угадывайте места города по фотографиям!

## 🎮 Как играть

1. Вам показывают фотографию места в Санкт-Петербурге
2. Отметьте на карте, где, по вашему мнению, находится это место
3. Чем ближе к реальной точке — тем больше очков (максимум 5000 за раунд)
4. Игра состоит из 5 раундов, максимальный счёт — 25000

## 🚀 Быстрый старт

### 1. Установка зависимостей

```bash
cd geoguessr-spb
python3 -m venv venv
source venv/bin/activate  # На Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Настройка переменных окружения

```bash
cp .env.example .env
```

Отредактируйте `.env` и добавьте:
- `SECRET_KEY` — секретный ключ Flask
- `YANDEX_MAPS_API_KEY` — API ключ Яндекс.Карт (получить на [developer.tech.yandex.ru](https://developer.tech.yandex.ru/))

### 3. Инициализация базы данных

```bash
python seed_db.py
```

### 4. Запуск приложения

```bash
python app.py
```

Откройте в браузере: [http://localhost:5000](http://localhost:5000)

## 📁 Структура проекта

```
geoguessr-spb/
├── app.py              # Flask приложение, API endpoints
├── models.py           # Модели базы данных (SQLAlchemy)
├── seed_db.py          # Скрипт заполнения БД локациями
├── requirements.txt    # Python зависимости
├── data/
│   └── locations.json  # Датасет локаций СПб (20 мест)
├── static/
│   ├── css/
│   │   └── style.css   # Стили интерфейса
│   └── js/
│       └── game.js     # Игровая логика (JS)
└── templates/
    └── index.html      # Главная страница
```

## 🏛️ Локации

В игре 20 известных мест Санкт-Петербурга:

- Эрмитаж (Зимний дворец)
- Петропавловская крепость
- Исаакиевский собор
- Спас на Крови
- Казанский собор
- Дворцовая площадь
- Медный всадник
- Дом Зингера
- Стрелка Васильевского острова
- Летний сад
- Крейсер Аврора
- Мариинский театр
- Смольный собор
- И другие...

## ⚙️ Технологии

- **Backend**: Python 3, Flask, SQLAlchemy
- **Frontend**: HTML5, CSS3, JavaScript (ES6+)
- **Карты**: Яндекс.Карты JavaScript API v3
- **База данных**: SQLite

## 📝 API Endpoints

| Метод | URL | Описание |
|-------|-----|----------|
| POST | `/api/game/start` | Начать новую игру |
| GET | `/api/game/location` | Получить текущую локацию |
| POST | `/api/game/guess` | Отправить координаты |
| GET | `/api/game/results` | Результаты текущей игры |
| GET | `/api/leaderboard` | Таблица лидеров |

## 🔧 Добавление своих локаций

Отредактируйте `data/locations.json` и добавьте новые места:

```json
{
    "name": "Название места",
    "description": "Описание",
    "latitude": 59.1234,
    "longitude": 30.1234,
    "image_url": "https://...",
    "difficulty": 1
}
```

Затем запустите `python seed_db.py` для обновления БД.

## 📄 Лицензия

MIT License
