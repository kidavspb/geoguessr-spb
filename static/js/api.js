/**
 * Обёртки над игровым API. Каждая возвращает { ok, status, data }.
 */

async function request(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', ...options });
    let data = null;
    try {
        data = await response.json();
    } catch (e) {
        // не-JSON ответ (например, 502 от прокси)
    }
    return { ok: response.ok, status: response.status, data };
}

function post(url, body) {
    return request(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
}

export const api = {
    startGame: (body) => post('/api/game/start', body),

    // peek=true — предзагрузка со следующего экрана: таймер раунда не стартует
    getLocation: (peek = false) =>
        request(peek ? '/api/game/location?peek=1' : '/api/game/location'),

    skipLocation: () => post('/api/game/skip_location', {}),

    setActualPoint: (latitude, longitude) =>
        post('/api/game/set_actual_point', { latitude, longitude }),

    guess: (payload) => post('/api/game/guess', payload),

    results: () => request('/api/game/results'),

    challengeInfo: (token) => request(`/api/challenge/${encodeURIComponent(token)}`),

    dailyInfo: () => request('/api/daily'),

    dailyLeaderboard: () => request('/api/daily/leaderboard'),

    playerStats: (name) => request(`/api/player/stats?name=${encodeURIComponent(name)}`),

    leaderboard: (params) => {
        const qs = params.toString();
        return request(qs ? `/api/leaderboard?${qs}` : '/api/leaderboard');
    }
};
