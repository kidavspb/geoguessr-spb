#!/usr/bin/env bash
# Сообщить о неудачном deployment (запускается через OnFailure=).
# Без настроенного канала только пишет в journal.
set -uo pipefail

readonly CONFIG='/etc/geoguessr-deploy/notify.env'
readonly STAMP='/var/lib/geoguessr-deploy/last-failure-notice'
readonly QUIET_MINUTES=60

log() {
    printf 'geoguessr-deploy-notify: %s\n' "$*"
}

if [[ ! -r "$CONFIG" ]]; then
    log "deployment failed; notifications are not configured ($CONFIG)"
    exit 0
fi
# shellcheck source=/dev/null
source "$CONFIG"
if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
    log "deployment failed; TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in $CONFIG"
    exit 0
fi

# Неудачный deploy повторяется каждые ~2 минуты: сообщаем не чаще раза в час.
if [[ -n "$(find "$STAMP" -mmin -"$QUIET_MINUTES" 2>/dev/null)" ]]; then
    log 'deployment failed; a notification was sent recently'
    exit 0
fi

details=$(journalctl -u geoguessr-deploy.service -n 15 --no-pager -o cat 2>/dev/null | tail -c 3000)
text=$(printf '⚠️ %s: deployment geoguessr-spb не удался\n\n%s' "$(hostname)" "$details")
if curl --fail --silent --show-error --max-time 15 \
    --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
    --data-urlencode "text=$text" \
    "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" > /dev/null; then
    touch "$STAMP"
    log 'failure notification sent'
else
    log 'WARNING: failure notification could not be sent'
fi
