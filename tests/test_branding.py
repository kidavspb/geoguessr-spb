import json
from pathlib import Path


def _assert_manifest_icons_exist(app, manifest_name):
    static_dir = Path(app.static_folder)
    manifest = json.loads((static_dir / manifest_name).read_text())
    for icon in manifest['icons']:
        assert icon['src'].startswith('/static/')
        assert (static_dir / icon['src'].removeprefix('/static/')).is_file()


def test_production_branding_is_default(client, app, monkeypatch):
    monkeypatch.setitem(app.config, 'FAVICON_VARIANT', 'prod')

    page = client.get('/').get_data(as_text=True)

    assert '/static/manifest.json?v=' in page
    assert '/static/favicons/prod/favicon.svg?v=' in page
    assert '/static/favicons/dev/' not in page
    assert client.get('/favicon.ico').data == (
        Path(app.static_folder) / 'favicons/prod/favicon.ico'
    ).read_bytes()
    _assert_manifest_icons_exist(app, 'manifest.json')


def test_dev_branding_uses_separate_assets(client, app, monkeypatch):
    monkeypatch.setitem(app.config, 'FAVICON_VARIANT', 'dev')

    page = client.get('/').get_data(as_text=True)
    admin_page = client.get('/admin').get_data(as_text=True)

    assert '/static/manifest-dev.json?v=' in page
    assert '/static/favicons/dev/favicon.svg?v=' in page
    assert '/static/favicons/dev/favicon.svg?v=' in admin_page
    assert client.get('/favicon.ico').data == (
        Path(app.static_folder) / 'favicons/dev/favicon.ico'
    ).read_bytes()
    _assert_manifest_icons_exist(app, 'manifest-dev.json')
