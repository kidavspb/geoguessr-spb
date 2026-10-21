"""Хэллоуин — временный сезонный режим «Ночь Медного всадника».

Сам режим целиком клиентский: это обычная игра с таймером, у которой отсчёт
нарисован как приближающийся всадник, а вход спрятан пасхалкой за логотипом.
Правила, счёт и таблица лидеров те же, поэтому сервер решает только одно —
показывать ли режим.

По умолчанию он включается сам каждый год в окно вокруг Хэллоуина по
петербургскому времени. HALLOWEEN_MODE=on/off принудительно включает или
выключает его: для проверки на DEV вне сезона, для тестов и как выключатель
без деплоя (достаточно рестарта).

Чтобы убрать режим совсем: этот модуль и tests/test_halloween.py,
static/js/halloween.js, static/img/halloween/, блоки {% if halloween %} в
templates/index.html, раздел «Хэллоуин» в static/css/style.css, вызовы из
static/js/main.js, tests/e2e/test_chase.py, сервер halloween_server в
tests/e2e/conftest.py и строки про режим в README, AGENTS и .env.example.
"""
from daily import today_msk

# Окно включительно, (месяц, день) по МСК.
FIRST_DAY = (10, 20)
LAST_DAY = (11, 1)

_ON = {'on', '1', 'true', 'yes'}
_OFF = {'off', '0', 'false', 'no'}


def halloween_active(mode='', today=None):
    """Показывать ли хэллоуинский режим. Неизвестный mode — по календарю."""
    mode = (mode or '').strip().lower()
    if mode in _ON:
        return True
    if mode in _OFF:
        return False
    today = today or today_msk()
    return FIRST_DAY <= (today.month, today.day) <= LAST_DAY
