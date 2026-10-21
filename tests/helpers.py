"""Общие помощники backend-тестов."""


def round_body(location, **fields):
    """Тело запроса раунда в том виде, в каком его шлёт настоящий клиент."""
    return {
        'round_id': location['round_id'],
        'location_version': location['location_version'],
        **fields,
    }


def guess_at_target(client, location, **fields):
    """Ответить точно в выданную точку раунда; вернуть JSON ответа.

    Как настоящий клиент, сначала отмечает показ панорамы (/ready): без него
    игра с таймером ответ не примет. Отказ /ready (район без проверенной
    панорамы) не проверяется — о нём так же сообщит сам /guess.
    """
    client.post('/api/game/ready', json=round_body(location))
    body = round_body(location, latitude=location['latitude'],
                      longitude=location['longitude'], **fields)
    return client.post('/api/game/guess', json=body).get_json()
