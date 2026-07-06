"""Обратное геокодирование через HTTP-геокодер Яндекса.

Опциональная косметика: если ключ не задан или сервис недоступен, игра
работает без адресов (клиент дополнительно умеет геокодировать сам через
JS API — см. clientReverseGeocode в game.js).
"""
import json
import logging
import os
import urllib.parse
import urllib.request
from functools import lru_cache

logger = logging.getLogger(__name__)


def _api_key():
    """Ключ HTTP-геокодера; отдельный сервис, но пробуем и ключ карт."""
    return (os.environ.get('YANDEX_GEOCODER_API_KEY')
            or os.environ.get('YANDEX_MAPS_API_KEY', ''))


@lru_cache(maxsize=1024)
def _reverse_geocode_cached(lat_r, lon_r):
    """Запрос к геокодеру. Координаты уже округлены (ключ кэша)."""
    params = urllib.parse.urlencode({
        'apikey': _api_key(),
        'geocode': f'{lon_r},{lat_r}',
        'sco': 'longlat',
        'kind': 'house',
        'format': 'json',
        'results': 1,
        'lang': 'ru_RU',
    })
    url = f'https://geocode-maps.yandex.ru/1.x/?{params}'
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.load(resp)
        members = data['response']['GeoObjectCollection']['featureMember']
        if not members:
            return None
        # name — «улица, дом»; этого достаточно, город игрок и так знает
        return members[0]['GeoObject'].get('name') or None
    except Exception as e:
        logger.debug('Геокодер недоступен: %s', e)
        return None


def reverse_geocode(lat, lon):
    """Адрес точки («улица, дом») или None, если геокодер недоступен/выключен."""
    if not _api_key():
        return None
    # Округление до ~10 м: соседние панорамы попадают в один ключ кэша
    return _reverse_geocode_cached(round(lat, 4), round(lon, 4))
