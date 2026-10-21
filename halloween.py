"""Хэллоуин — временный сезонный режим «Побег от Медного всадника».

Сама погоня целиком клиентская: это обычная игра с таймером на минуту, у
которой отсчёт нарисован как приближающийся всадник. Правила, счёт и таблица
лидеров те же, поэтому сервер решает только одно — показывать ли режим.

По умолчанию он включается сам каждый год в окно вокруг Хэллоуина по
петербургскому времени. HALLOWEEN_MODE=on/off принудительно включает или
выключает его: для проверки на DEV вне сезона, для тестов и как выключатель
без деплоя (достаточно рестарта).

Чтобы убрать режим совсем: этот модуль и его тест, static/js/halloween.js,
блоки {% if halloween %} в templates/index.html, раздел «Хэллоуин» в
static/css/style.css и вызовы из static/js/main.js.
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
