/**
 * Надёжные обёртки над игровым API.
 *
 * Каждый запрос имеет конечный таймаут. Безопасные GET и идемпотентные POST,
 * привязанные к round_id, один раз повторяются после короткой паузы.
 */

const DEFAULT_TIMEOUT_MS = 10000;

function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function request(url, options = {}, config = {}) {
    const timeoutMs = config.timeoutMs || DEFAULT_TIMEOUT_MS;
    const retries = config.retries || 0;

    for (let attempt = 0; attempt <= retries; attempt++) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(url, {
                credentials: 'same-origin',
                ...options,
                signal: controller.signal
            });
            let data = null;
            try {
                data = await response.json();
            } catch (error) {
                // Не-JSON ответ прокси всё равно превращаем в понятный результат.
            }

            const retryableStatus = [429, 502, 503, 504].includes(response.status);
            if (!response.ok && retryableStatus && attempt < retries) {
                await delay(250 * (attempt + 1));
                continue;
            }
            return { ok: response.ok, status: response.status, data };
        } catch (error) {
            if (attempt < retries) {
                await delay(250 * (attempt + 1));
                continue;
            }
            const timedOut = error && error.name === 'AbortError';
            return {
                ok: false,
                status: 0,
                data: { error: timedOut ? 'Сервер отвечает слишком долго' : 'Нет соединения с сервером' },
                networkError: timedOut ? 'timeout' : 'network'
            };
        } finally {
            clearTimeout(timeout);
        }
    }
}

function post(url, body, config = {}) {
    return request(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    }, config);
}

export const api = {
    startGame: body => post('/api/game/start', body, { timeoutMs: 15000 }),

    getLocation: (peek = false) => request(
        peek ? '/api/game/location?peek=1' : '/api/game/location',
        {},
        { retries: 1 }
    ),

    skipLocation: (roundId, reason = 'no_coverage', locationVersion = null) =>
        post('/api/game/skip_location', {
            round_id: roundId,
            reason,
            location_version: locationVersion
        }, { retries: locationVersion == null ? 0 : 1 }),

    roundReady: roundId =>
        post('/api/game/ready', { round_id: roundId }, { retries: 1 }),

    guess: payload => post('/api/game/guess', payload, {
        timeoutMs: 12000,
        retries: payload && payload.round_id ? 1 : 0
    }),

    setRoundAddress: (roundId, address) =>
        post('/api/game/set_address', { round_id: roundId, address }, { retries: 1 }),

    panoramaMetric: payload =>
        post('/api/game/panorama_metric', payload),

    results: () => request('/api/game/results', {}, { retries: 1 }),

    challengeInfo: token =>
        request(`/api/challenge/${encodeURIComponent(token)}`, {}, { retries: 1 }),

    dailyInfo: () => request('/api/daily', {}, { retries: 1 }),

    dailyLeaderboard: () => request('/api/daily/leaderboard', {}, { retries: 1 }),

    playerStats: name =>
        request(`/api/player/stats?name=${encodeURIComponent(name)}`, {}, { retries: 1 }),

    leaderboard: params => {
        const qs = params.toString();
        return request(qs ? `/api/leaderboard?${qs}` : '/api/leaderboard', {}, { retries: 1 });
    }
};
