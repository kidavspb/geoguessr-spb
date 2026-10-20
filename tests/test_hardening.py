"""Ошибки API в JSON, предел размера запроса, имя игрока и ключ подписи."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_oversized_body_gets_json_413(client):
    response = client.post('/api/game/start', data='x' * (32 * 1024),
                           content_type='application/json')
    assert response.status_code == 413
    assert response.get_json() == {'error': 'Слишком большой запрос'}


def test_deeply_nested_json_is_a_bad_request(client):
    # 15 000 скобок помещаются в лимит тела и вызывают RecursionError в json.
    response = client.post('/api/game/start', data='[' * 15000,
                           content_type='application/json')
    assert response.status_code == 400
    assert 'error' in response.get_json()


def test_unknown_api_path_gets_json_404(client):
    response = client.get('/api/does-not-exist')
    assert response.status_code == 404
    assert response.get_json() == {'error': 'Не найдено'}


def test_unexpected_api_error_gets_json_500(client, app, app_module, monkeypatch):
    def broken_today():
        raise RuntimeError('boom')

    monkeypatch.setattr(app_module, 'today_msk', broken_today)
    monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)
    response = client.get('/api/daily')
    assert response.status_code == 500
    assert response.get_json() == {'error': 'Ошибка сервера'}


def test_player_name_is_stored_as_one_clean_line(client):
    client.post('/api/game/start', json={
        'player_name': '  eve\nINFO Игра начата:\tplayer=admin\x00  ',
    })
    assert client.get('/api/game/results').get_json()['player_name'] == (
        'eve INFO Игра начата: player=admin'
    )


def _import_app(tmp_path, **env):
    return subprocess.run(
        [sys.executable, '-c', 'import app'],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
        env={
            **os.environ,
            'DATABASE_URL': f'sqlite:///{tmp_path / "import.db"}',
            'AUTO_MIGRATE': 'false',
            **env,
        },
    )


@pytest.mark.parametrize('secret', ['', 'your-secret-key-here'])
def test_app_refuses_to_start_without_real_secret_key(tmp_path, secret):
    result = _import_app(tmp_path, SECRET_KEY=secret, FLASK_DEBUG='false')
    assert result.returncode != 0
    assert 'SECRET_KEY' in result.stderr


def test_local_debug_starts_with_random_secret_key(tmp_path):
    result = _import_app(tmp_path, SECRET_KEY='', FLASK_DEBUG='true')
    assert result.returncode == 0, result.stderr


def test_client_reports_unavailable_maps_sdk_to_the_log(client, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        response = client.post('/api/client/sdk_unavailable', json={'sdk': 'v2'},
                               headers={'User-Agent': 'TestBrowser/1.0'})
    assert response.status_code == 204
    assert any('SDK Яндекса' in record.getMessage() and 'v2' in record.getMessage()
               and 'TestBrowser/1.0' in record.getMessage() for record in caplog.records)


def test_sdk_report_ignores_unknown_sdk_names(client, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        response = client.post('/api/client/sdk_unavailable', json={'sdk': '<script>'})
    assert response.status_code == 204
    assert '<script>' not in caplog.text


def test_panorama_metric_accepts_sdk_unavailable(client, app_module):
    from helpers import round_body
    from models import GameRound
    location = client.post('/api/game/start', json={}).get_json()['location']
    response = client.post('/api/game/panorama_metric', json=round_body(
        location, status='sdk_unavailable', lookup_ms=12, attempts=0,
    ))
    assert response.status_code == 200
    with app_module.app.app_context():
        rnd = app_module.db.session.get(GameRound, location['round_id'])
        assert rnd.panorama_status == 'sdk_unavailable'
