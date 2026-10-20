#!/usr/bin/env bash
set -Eeuo pipefail

export GIT_TERMINAL_PROMPT=0

readonly APP_DIR='/root/geoguessr-spb'
readonly VENV_DIR="$APP_DIR/venv"
readonly APP_SERVICE='geoguessr.service'
readonly EXPECTED_BRANCH='main'
readonly EXPECTED_ORIGIN='https://github.com/kidavspb/geoguessr-spb.git'
readonly STATE_DIR='/var/lib/geoguessr-deploy'
readonly MARKER_FILE="$STATE_DIR/deployed-sha"
readonly LOCK_FILE='/run/lock/geoguessr-deploy.lock'
readonly BACKUP_DIR='/var/backups/geoguessr'
readonly BACKUP_KEEP=30
readonly HEALTH_URL='http://127.0.0.1:8000/healthz'
readonly HEALTH_ATTEMPTS=20
readonly HEALTH_DELAY_SECONDS=1
readonly HEALTH_TIMEOUT_SECONDS=5

stage='initialization'
marker_tmp=''
requirements_tmp=''
backup_tmp=''
current_sha=''
checkout_updated=false
service_restarted=false

log() {
    printf 'geoguessr-deploy: %s\n' "$*"
}

# Консистентная копия живой SQLite (WAL) через backup API, а не cp файла.
# Код приложения не импортируется: после неудачного restart checkout может
# содержать сломанную версию, а бэкап не должен мешать её исправить.
# Копия пишется во временный файл и переименовывается только целиком.
backup_database() {
    backup_tmp="$1.partial"
    "$VENV_DIR/bin/python" - "$APP_DIR" "$backup_tmp" <<'PY'
import pathlib
import sqlite3
import sys

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

app_dir = pathlib.Path(sys.argv[1])
# Та же логика, что в app.py и Flask-SQLAlchemy: DATABASE_URL из .env,
# относительный путь SQLite — внутри instance/.
url = make_url(dotenv_values(app_dir / '.env').get('DATABASE_URL')
               or 'sqlite:///geoguessr_spb.db')
if url.get_backend_name() != 'sqlite' or not url.database:
    sys.exit(f'backup supports only file SQLite, got {url.render_as_string()}')
path = pathlib.Path(url.database)
if not path.is_absolute():
    path = app_dir / 'instance' / path
# mode=rw не создаёт пустую БД, если путь вдруг указывает не туда.
source = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
target.close()
source.close()
PY
    chmod 0600 "$backup_tmp"
    mv -f -- "$backup_tmp" "$1"
    backup_tmp=''
}

on_exit() {
    local exit_code=$?
    if [[ -n "$marker_tmp" && -e "$marker_tmp" ]]; then
        rm -f -- "$marker_tmp"
    fi
    if [[ -n "$requirements_tmp" && -e "$requirements_tmp" ]]; then
        rm -f -- "$requirements_tmp"
    fi
    if [[ -n "$backup_tmp" && -e "$backup_tmp" ]]; then
        rm -f -- "$backup_tmp"
    fi
    if (( exit_code != 0 )); then
        log "ERROR stage=$stage exit=$exit_code"
        # Работающий процесс ещё исполняет прежний commit, а шаблоны и статика
        # отдаются с диска. Возвращаем checkout к этому commit, чтобы до
        # следующей попытки сайт не отдавал новый фронтенд старому backend.
        # После restart откат кода не выполняется: схема БД уже могла измениться.
        if [[ "$checkout_updated" == true && "$service_restarted" != true ]]; then
            if git -C "$APP_DIR" reset --hard --quiet "$current_sha"; then
                log "checkout returned to running commit $current_sha"
            else
                log "WARNING: could not return checkout to $current_sha"
            fi
        fi
    fi
}
trap on_exit EXIT

stage='preflight'
if (( EUID != 0 )); then
    log 'must run as root'
    exit 1
fi

stage='lock'
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log 'another deployment is already running; skipping'
    exit 0
fi

stage='preflight'
if [[ ! -d "$APP_DIR/.git" ]]; then
    log "Git repository not found: $APP_DIR"
    exit 1
fi
if [[ ! -x "$VENV_DIR/bin/pip" || ! -x "$VENV_DIR/bin/flask" ]]; then
    log "Python environment is incomplete: $VENV_DIR"
    exit 1
fi
if [[ ! -d "$STATE_DIR" || ! -f "$MARKER_FILE" ]]; then
    log "deployment marker is not initialized: $MARKER_FILE"
    exit 1
fi

cd "$APP_DIR"

current_branch=$(git branch --show-current)
if [[ "$current_branch" != "$EXPECTED_BRANCH" ]]; then
    log "expected branch $EXPECTED_BRANCH, found ${current_branch:-detached-HEAD}"
    exit 1
fi

origin_url=$(git remote get-url origin)
if [[ "$origin_url" != "$EXPECTED_ORIGIN" ]]; then
    log "unexpected origin URL: $origin_url"
    exit 1
fi

tracked_changes=$(git status --porcelain --untracked-files=no)
if [[ -n "$tracked_changes" ]]; then
    log 'tracked working tree is not clean:'
    printf '%s\n' "$tracked_changes"
    exit 1
fi

deployed_sha=$(tr -d '\n' < "$MARKER_FILE")
if [[ ! "$deployed_sha" =~ ^[0-9a-f]{40}$ ]]; then
    log "invalid deployed SHA marker: $MARKER_FILE"
    exit 1
fi
current_sha=$(git rev-parse --verify 'HEAD^{commit}')

stage='fetch'
git fetch --prune origin '+refs/heads/main:refs/remotes/origin/main'
origin_sha=$(git rev-parse --verify 'refs/remotes/origin/main^{commit}')
if [[ ! "$origin_sha" =~ ^[0-9a-f]{40}$ ]]; then
    log 'origin/main did not resolve to a valid commit'
    exit 1
fi

log "deployed-sha=$deployed_sha origin-main=$origin_sha"
if [[ "$origin_sha" == "$deployed_sha" ]]; then
    if [[ "$current_sha" != "$deployed_sha" ]]; then
        log "HEAD does not match the deployed marker: HEAD=$current_sha marker=$deployed_sha"
        exit 1
    fi
    log 'no new version'
    exit 0
fi

stage='fast-forward-check'
if ! git merge-base --is-ancestor "$deployed_sha" "$current_sha"; then
    log "HEAD is behind or unrelated to the deployed marker: marker=$deployed_sha HEAD=$current_sha"
    exit 1
fi
if ! git merge-base --is-ancestor "$current_sha" "$origin_sha"; then
    log "fast-forward is impossible: HEAD=$current_sha origin/main=$origin_sha"
    exit 1
fi

# Повторы неудачного deploy (каждые ~2 минуты) не делают новых копий: иначе
# ротация вытеснила бы снимок, снятый до первой попытки.
stage='backup'
install -d -m 0700 "$BACKUP_DIR"
attempt_id="${current_sha:0:12}-${origin_sha:0:12}"
existing_backup=$(find "$BACKUP_DIR" -maxdepth 1 -name "*-$attempt_id.db" -print -quit)
if [[ -n "$existing_backup" ]]; then
    log "database backup for this attempt already exists: $existing_backup"
else
    backup_file="$BACKUP_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$attempt_id.db"
    backup_database "$backup_file"
    log "database backup: $backup_file"
fi
find "$BACKUP_DIR" -maxdepth 1 -name '*.db' -type f -printf '%T@ %p\n' \
    | sort -rn | tail -n +"$((BACKUP_KEEP + 1))" | cut -d' ' -f2- \
    | xargs -r rm -f --

# Зависимости новой версии ставятся до обновления checkout: долгий pip не
# растягивает окно, в котором старый процесс отдаёт новые шаблоны с диска.
stage='dependencies'
requirements_tmp=$(mktemp)
git show "$origin_sha:requirements.txt" > "$requirements_tmp"
"$VENV_DIR/bin/pip" install --disable-pip-version-check \
    --requirement "$requirements_tmp"

stage='fast-forward'
log "deploying $origin_sha"
checkout_updated=true
git merge --ff-only "$origin_sha"
if [[ "$(git rev-parse HEAD)" != "$origin_sha" ]]; then
    log 'HEAD does not match fetched origin/main after merge'
    exit 1
fi

stage='migrations'
AUTO_MIGRATE=false "$VENV_DIR/bin/flask" --app app.py db upgrade

stage='restart'
service_restarted=true
systemctl restart "$APP_SERVICE"

stage='healthcheck'
health_ok=false
for (( attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++ )); do
    if curl --fail --silent --show-error \
        --max-time "$HEALTH_TIMEOUT_SECONDS" \
        --output /dev/null "$HEALTH_URL"; then
        health_ok=true
        break
    fi
    log "healthcheck attempt $attempt/$HEALTH_ATTEMPTS failed"
    sleep "$HEALTH_DELAY_SECONDS"
done

if [[ "$health_ok" != true ]]; then
    log "healthcheck failed: $HEALTH_URL"
    systemctl status "$APP_SERVICE" --no-pager || true
    exit 1
fi

stage='marker'
marker_tmp=$(mktemp "$STATE_DIR/.deployed-sha.XXXXXX")
printf '%s\n' "$origin_sha" > "$marker_tmp"
chmod 0644 "$marker_tmp"
mv -f -- "$marker_tmp" "$MARKER_FILE"
marker_tmp=''

stage='complete'
log "deployment successful: $origin_sha"
