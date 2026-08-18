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
readonly HEALTH_URL='http://127.0.0.1:8000/'
readonly HEALTH_ATTEMPTS=20
readonly HEALTH_DELAY_SECONDS=1
readonly HEALTH_TIMEOUT_SECONDS=5

stage='initialization'
marker_tmp=''

log() {
    printf 'geoguessr-deploy: %s\n' "$*"
}

on_exit() {
    local exit_code=$?
    if [[ -n "$marker_tmp" && -e "$marker_tmp" ]]; then
        rm -f -- "$marker_tmp"
    fi
    if (( exit_code != 0 )); then
        log "ERROR stage=$stage exit=$exit_code"
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

stage='fast-forward'
log "deploying $origin_sha"
git merge --ff-only "$origin_sha"
if [[ "$(git rev-parse HEAD)" != "$origin_sha" ]]; then
    log 'HEAD does not match fetched origin/main after merge'
    exit 1
fi

stage='dependencies'
"$VENV_DIR/bin/pip" install --disable-pip-version-check \
    --requirement "$APP_DIR/requirements.txt"

stage='migrations'
"$VENV_DIR/bin/flask" --app app.py db upgrade

stage='restart'
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
