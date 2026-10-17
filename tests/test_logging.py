"""INFO-события приложения и модулей доходят до журнала ровно один раз."""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LOG_BOTH = (
    "import logging, app; "
    "app.app.logger.info('app-info-event'); "
    "logging.getLogger('pool').info('pool-info-event')"
)


def _run(tmp_path, **env):
    return subprocess.run(
        [sys.executable, '-c', LOG_BOTH],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
        env={
            **os.environ,
            'DATABASE_URL': f'sqlite:///{tmp_path / "log.db"}',
            'AUTO_MIGRATE': 'false',
            'SECRET_KEY': 'log-test-secret',
            **env,
        },
    )


def test_info_events_reach_the_log_once(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    # Раньше Flask без debug пропускал только WARNING+, и эти строки терялись.
    assert result.stderr.count('app-info-event') == 1
    assert result.stderr.count('pool-info-event') == 1


def test_log_level_is_configurable(tmp_path):
    result = _run(tmp_path, LOG_LEVEL='warning')
    assert result.returncode == 0, result.stderr
    assert 'app-info-event' not in result.stderr
