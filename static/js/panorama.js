/**
 * Панорамы Яндекса: один поиск ближайшей съёмки, отменяемая подготовка
 * следующего раунда и дешёвый прогрев низкодетализированных тайлов.
 */
import { state, MAX_PANORAMA_RETRIES } from './state.js';
import { showToast } from './utils.js';
import { api } from './api.js';
import { reloadFailedScript } from './sdk.js';

const PLAYER_OPTIONS = {
    controls: ['zoomControl'],
    direction: [0, 0],
    span: [130, 80],
    suppressMapOpenBlock: true
};

const API_READY_TIMEOUT_MS = 12000;
const LOCATE_TIMEOUT_MS = 7000;
const PLAYER_OPEN_TIMEOUT_MS = 10000;
const NETWORK_RETRY_DELAY_MS = 300;
const MAX_WARM_TILES = 8;

let v2ReadyPromise = null;

function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function distanceKm(lat1, lon1, lat2, lon2) {
    const toRad = value => value * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 +
        Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    const clamped = Math.min(1, Math.max(0, a));
    return 6371 * 2 * Math.atan2(Math.sqrt(clamped), Math.sqrt(1 - clamped));
}

function withTimeout(value, timeoutMs, message) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(message)), timeoutMs);
        Promise.resolve(value).then(
            result => {
                clearTimeout(timer);
                resolve(result);
            },
            error => {
                clearTimeout(timer);
                reject(error);
            }
        );
    });
}

function setOverlayActions({ retry = false, skip = false } = {}) {
    const actions = document.getElementById('photo-overlay-actions');
    if (!actions) return;
    document.getElementById('retry-panorama-btn').classList.toggle('hidden', !retry);
    document.getElementById('skip-panorama-btn').classList.toggle('hidden', !skip);
    actions.classList.toggle('hidden', !retry && !skip);
}

/** Оверлей появляется с задержкой, чтобы прогретый раунд не мигал. */
export function scheduleLoadingOverlay() {
    clearTimeout(state.overlayTimer);
    setOverlayActions();
    state.overlayTimer = setTimeout(() => {
        const overlay = document.getElementById('photo-overlay');
        overlay.querySelector('span').textContent = 'Загрузка панорамы…';
        overlay.classList.remove('hidden');
    }, 250);
}

export function showLoadingOverlay(text, actions = {}) {
    clearTimeout(state.overlayTimer);
    const overlay = document.getElementById('photo-overlay');
    overlay.querySelector('span').textContent = text;
    setOverlayActions(actions);
    overlay.classList.remove('hidden');
}

function hideLoadingOverlay() {
    clearTimeout(state.overlayTimer);
    setOverlayActions();
    document.getElementById('photo-overlay').classList.add('hidden');
}

/** Дождаться асинхронно подключённого JS API v2. */
export function ymapsV2Ready() {
    if (v2ReadyPromise) return v2ReadyPromise;

    v2ReadyPromise = new Promise((resolve, reject) => {
        const started = performance.now();
        let scriptRetried = false;
        const check = () => {
            if (window.yandexMapsLoadErrors && window.yandexMapsLoadErrors.v2) {
                const reloading = !scriptRetried
                    ? reloadFailedScript('yandex-maps-v2', 'v2') : null;
                if (reloading) {
                    scriptRetried = true;
                    reloading.then(check, reject);
                    return;
                }
                reject(new Error('API панорам не загрузился'));
                return;
            }
            if (typeof ymaps !== 'undefined' && ymaps.ready) {
                try {
                    ymaps.ready(resolve);
                } catch (error) {
                    reject(error);
                }
                return;
            }
            if (performance.now() - started >= API_READY_TIMEOUT_MS) {
                reject(new Error('Таймаут загрузки API панорам'));
                return;
            }
            setTimeout(check, 100);
        };
        check();
    }).catch(error => {
        // Следующая ручная попытка после сетевого восстановления должна иметь
        // возможность снова дождаться API.
        v2ReadyPromise = null;
        throw error;
    });
    return v2ReadyPromise;
}

/**
 * Один locate уже ищет ближайшую панораму вокруг координаты. Пустой успешный
 * ответ и транспортная ошибка принципиально различаются.
 */
export async function locatePanorama(latitude, longitude) {
    try {
        const result = await withTimeout(
            ymaps.panorama.locate([latitude, longitude]),
            LOCATE_TIMEOUT_MS,
            'Таймаут поиска панорамы'
        );
        if (result && result.length) {
            return { status: 'ready', panorama: result[0] };
        }
        return { status: 'no_coverage', panorama: null };
    } catch (error) {
        return { status: 'network_error', panorama: null, error };
    }
}

function metricFor(prepared, status, readyMs = null) {
    if (!prepared || !prepared.location || !prepared.location.round_id) return;
    api.panoramaMetric({
        round_id: prepared.location.round_id,
        status,
        lookup_ms: prepared.lookupMs,
        ready_ms: readyMs,
        attempts: prepared.attempts
    });
}

/**
 * Найти рабочую панораму. Новая случайная точка всегда получает честную
 * попытку; после подтверждённого отсутствия сервер выдаёт точку восстановления
 * из пула. Сетевой сбой повторяется один раз на той же точке и не портит пул.
 */
async function prepareRound(initialLocation, task = null) {
    const started = performance.now();
    let location = initialLocation;
    let attempts = 0;
    let skips = 0;

    try {
        await ymapsV2Ready();
    } catch (error) {
        return {
            ok: false, status: 'api_error', error, location,
            attempts, lookupMs: Math.round(performance.now() - started)
        };
    }

    if (ymaps.panorama && typeof ymaps.panorama.isSupported === 'function' &&
            !ymaps.panorama.isSupported()) {
        return {
            ok: false, status: 'unsupported', location,
            attempts, lookupMs: Math.round(performance.now() - started)
        };
    }

    while (skips <= MAX_PANORAMA_RETRIES) {
        if (task && task.cancelled) {
            return {
                ok: false, status: 'cancelled', location,
                attempts, lookupMs: Math.round(performance.now() - started)
            };
        }

        let located = null;
        for (let networkTry = 0; networkTry < 2; networkTry++) {
            attempts++;
            located = await locatePanorama(location.latitude, location.longitude);
            if (located.status !== 'network_error' || networkTry === 1) break;
            await delay(NETWORK_RETRY_DELAY_MS);
        }

        if (located.status === 'ready') {
            const panorama = located.panorama;
            const position = panorama.getPosition().slice(0, 2);
            const drift = distanceKm(
                location.latitude, location.longitude, position[0], position[1]
            );
            const maxDrift = Number(location.max_panorama_drift_km) || 1;
            if (drift <= maxDrift) {
                return {
                    ok: true,
                    status: 'ready',
                    location,
                    panorama,
                    position,
                    attempts,
                    lookupMs: Math.round(performance.now() - started)
                };
            }
            // locate возвращает ближайшую съёмку, но в редкой пустой зоне она
            // может оказаться слишком далеко от загаданного места. Такой Player
            // дал бы визуально один адрес, а сервер считал бы по другому.
            located = { status: 'no_coverage', panorama: null };
        }

        if (located.status === 'network_error') {
            return {
                ok: false, status: 'network_error', error: located.error,
                location, attempts,
                lookupMs: Math.round(performance.now() - started)
            };
        }

        if (skips >= MAX_PANORAMA_RETRIES) {
            return {
                ok: false, status: 'no_coverage', location, attempts,
                lookupMs: Math.round(performance.now() - started)
            };
        }

        const skipped = await api.skipLocation(
            location.round_id, 'no_coverage', location.location_version
        );
        if (!skipped.ok || !skipped.data) {
            return {
                ok: false,
                status: skipped.networkError ? 'network_error' : 'api_error',
                location,
                attempts,
                lookupMs: Math.round(performance.now() - started)
            };
        }
        location = skipped.data;
        skips++;
    }
}

/**
 * Прогреть самый дешёвый уровень детализации напрямую через публичные URL
 * тайлов Panorama. В отличие от скрытого Player это не создаёт Canvas/WebGL,
 * но даёт следующему видимому Player мгновенную низкодетализированную картинку.
 */
function warmPanoramaTiles(prepared, task) {
    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (connection && (connection.saveData || /(^|-)2g$/.test(connection.effectiveType || ''))) {
        return;
    }
    const panorama = prepared.panorama;
    if (!panorama || typeof panorama.getTileLevels !== 'function' ||
            typeof panorama.getTileSize !== 'function') return;

    try {
        const tileSize = panorama.getTileSize();
        const candidates = panorama.getTileLevels().map(level => {
            const imageSize = level.getImageSize();
            const columns = Math.ceil(imageSize[0] / tileSize[0]);
            const rows = Math.ceil(imageSize[1] / tileSize[1]);
            return { level, columns, rows, count: columns * rows };
        }).sort((a, b) => a.count - b.count);
        if (!candidates.length) return;

        const selected = candidates[0];
        task.tileImages = [];
        for (let y = 0; y < selected.rows; y++) {
            for (let x = 0; x < selected.columns; x++) {
                if (task.tileImages.length >= MAX_WARM_TILES || task.cancelled) return;
                const image = new Image();
                image.decoding = 'async';
                if ('fetchPriority' in image) image.fetchPriority = 'low';
                image.src = selected.level.getTileUrl(x, y);
                task.tileImages.push(image);
            }
        }
        // Ссылки нужны только до завершения запросов; HTTP-кэш останется.
        setTimeout(() => {
            if (!task.cancelled) task.tileImages = [];
        }, 6000);
    } catch (error) {
        // Прогрев — оптимизация: нестандартная панорама не ломает раунд.
        task.tileImages = [];
    }
}

/** Создать единственную задачу подготовки следующего раунда. */
export function prefetchNextRound(location = null) {
    if (location && state.preloaded &&
            state.preloaded.roundId === location.round_id &&
            !state.preloaded.cancelled) {
        return state.preloaded.promise;
    }
    discardPreloaded(state.preloaded);
    state.preloaded = null;
    if (!location || !location.round_id) return null;

    const task = {
        roundId: location.round_id,
        location,
        cancelled: false,
        tileImages: [],
        prepared: null,
        promise: null
    };
    task.promise = prepareRound(location, task).then(prepared => {
        if (task.cancelled) return null;
        task.prepared = prepared;
        if (prepared && prepared.ok) warmPanoramaTiles(prepared, task);
        return prepared;
    }).catch(error => ({
        ok: false, status: 'api_error', error, location,
        attempts: 0, lookupMs: 0
    }));
    state.preloaded = task;
    return task.promise;
}

/** Отменить только действительно устаревшую подготовку. */
export function discardPreloaded(preloaded) {
    if (!preloaded) return;
    preloaded.cancelled = true;
    (preloaded.tileImages || []).forEach(image => {
        try { image.removeAttribute('src'); } catch (error) { /* запрос уже завершён */ }
    });
    preloaded.tileImages = [];
}

async function openPlayer(container, panorama, options) {
    if (panorama && typeof panorama.createPlayer === 'function') {
        const opening = Promise.resolve(panorama.createPlayer(container, options));
        try {
            return await withTimeout(
                opening,
                PLAYER_OPEN_TIMEOUT_MS,
                'Таймаут открытия панорамы'
            );
        } catch (error) {
            // Promise API Яндекса нельзя отменить. Если он всё же завершится
            // после нашего таймаута, сразу освободим запоздавший WebGL Player.
            opening.then(player => {
                try { player.destroy(); } catch (destroyError) { /* уже закрыт */ }
            }).catch(() => {});
            throw error;
        }
    }
    // Заглушка E2E и старые реализации API не имеют Panorama.createPlayer.
    return new ymaps.panorama.Player(container, panorama, options);
}

function nextPaint() {
    return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

/**
 * Открыть раунд. Если prefetch ещё идёт, используется ровно тот же Promise —
 * второго locate и второго комплекта тайлов не возникает.
 */
export async function loadPanorama(location, preloadTask = null) {
    const visibleStarted = performance.now();
    const loadId = ++state.roundLoadId;
    disposePanoramaPlayer();
    state.actualPoint = null;

    const container = document.getElementById('panorama-player');
    container.innerHTML = '';

    let prepared;
    if (preloadTask && preloadTask.roundId === location.round_id) {
        prepared = preloadTask.prepared || await preloadTask.promise;
        // Задача потреблена, но уже начатые Image-запросы не прерываем: Player
        // переиспользует их или получит тайлы из HTTP-кэша.
        state.preloaded = null;
        // Фоновая сеть могла кратко пропасть именно во время экрана
        // результата. Видимый переход получает свежую попытку на той же
        // версии точки, а идемпотентный skip не перескочит лишний раз.
        if (prepared && !prepared.ok && prepared.status === 'network_error') {
            prepared = await prepareRound(prepared.location || location);
        }
    } else {
        prepared = await prepareRound(location);
    }

    if (loadId !== state.roundLoadId) return { ok: false, status: 'cancelled' };
    if (!prepared || !prepared.ok) {
        const status = prepared ? prepared.status : 'api_error';
        metricFor(prepared || { location, attempts: 0, lookupMs: 0 }, status);
        if (status === 'unsupported') {
            showLoadingOverlay('Этот браузер не поддерживает панорамы Яндекса.');
        } else if (status === 'no_coverage') {
            showLoadingOverlay('Не удалось найти съёмку рядом.', { retry: true, skip: true });
        } else {
            showLoadingOverlay('Панорама пока не загрузилась. Проверьте соединение.',
                               { retry: true, skip: false });
        }
        return { ok: false, status, location: prepared ? prepared.location : location };
    }

    state.currentLocation = prepared.location;
    state.currentRoundId = prepared.location.round_id;
    state.lastPanorama = prepared.panorama;
    state.panoStartPoint = prepared.position;
    state.actualPoint = prepared.position;
    state.noMoveWarned = false;

    try {
        const player = await openPlayer(container, prepared.panorama, PLAYER_OPTIONS);
        if (loadId !== state.roundLoadId) {
            try { player.destroy(); } catch (error) { /* уже закрыт */ }
            return { ok: false, status: 'cancelled' };
        }
        state.panoramaPlayer = player;
        if (player.events && typeof player.events.add === 'function') {
            player.events.add('error', () => {
                if (state.currentRoundId === prepared.location.round_id) {
                    showLoadingOverlay('Яндекс не смог дорисовать панораму.', { retry: true });
                }
            });
        }
        startNoMoveWatchdog();
        // Запускаем фиксацию дедлайна параллельно первому paint: сеть не должна
        // задерживать появление уже открытого Player.
        const readyPromise = api.roundReady(prepared.location.round_id);
        await nextPaint();
        if (loadId !== state.roundLoadId) {
            return { ok: false, status: 'cancelled' };
        }
        hideLoadingOverlay();
        const readyMs = Math.round(performance.now() - visibleStarted);
        metricFor(prepared, 'ready', readyMs);
        return {
            ok: true,
            status: 'ready',
            location: prepared.location,
            // Сеть не держит уже интерактивный экран. Клиент начинает отсчёт
            // от первого кадра, а Promise ниже уточнит его серверным временем.
            deadlineMs: state.gameData.timeLimit
                ? Date.now() + state.gameData.timeLimit * 1000 : null,
            readyPromise,
            loadId,
            readyMs
        };
    } catch (error) {
        if (loadId !== state.roundLoadId) {
            return { ok: false, status: 'cancelled' };
        }
        metricFor(prepared, 'api_error');
        showLoadingOverlay('Не удалось открыть панораму.', { retry: true });
        return { ok: false, status: 'api_error', location: prepared.location };
    }
}

function startNoMoveWatchdog() {
    stopNoMoveWatchdog();
    if (!state.gameData.noMove || !state.panoramaPlayer) return;
    if (state.panoramaPlayer.events) {
        state.panoramaPlayer.events.add('panoramachange', enforceNoMove);
    }
    state.noMoveWatchdog = setInterval(enforceNoMove, 900);
}

function stopNoMoveWatchdog() {
    if (state.noMoveWatchdog) {
        clearInterval(state.noMoveWatchdog);
        state.noMoveWatchdog = null;
    }
}

function enforceNoMove() {
    if (document.hidden) return;
    const player = state.panoramaPlayer;
    const start = state.panoStartPoint;
    if (!player || !start) return;
    try {
        const pos = player.getPanorama().getPosition();
        const drifted = Math.abs(pos[0] - start[0]) > 0.00005 ||
                        Math.abs(pos[1] - start[1]) > 0.00005;
        if (!drifted) return;
        player.moveTo(start);
        if (!state.noMoveWarned) {
            state.noMoveWarned = true;
            showToast('🚷 Режим «не сходя с места» — ходить нельзя, только осматриваться');
        }
    } catch (error) {
        // Плеер в переходном состоянии — проверим на следующем редком тике.
    }
}

export async function returnToPanoStart() {
    if (!state.panoramaPlayer || !state.panoStartPoint) return;
    const loadId = state.roundLoadId;
    const panorama = state.lastPanorama;
    try {
        await state.panoramaPlayer.moveTo(state.panoStartPoint);
    } catch (error) {
        if (loadId !== state.roundLoadId) return;
        console.error('Не удалось вернуться к началу:', error);
        if (panorama) {
            const container = document.getElementById('panorama-player');
            disposePanoramaPlayer();
            container.innerHTML = '';
            try {
                const player = await openPlayer(container, panorama, PLAYER_OPTIONS);
                if (loadId !== state.roundLoadId) {
                    try { player.destroy(); } catch (destroyError) { /* уже закрыт */ }
                    return;
                }
                state.panoramaPlayer = player;
                startNoMoveWatchdog();
            } catch (openError) {
                if (loadId === state.roundLoadId) {
                    showLoadingOverlay('Не удалось восстановить панораму.', { retry: true });
                }
            }
        }
    }
}

/**
 * На результате оставляем лёгкую кнопку вместо второго живого Player. Полная
 * панорама по-прежнему открывается по нажатию.
 */
export function showResultPano() {
    const container = document.getElementById('result-pano');
    destroyResultPano();
    container.innerHTML = '';
    if (!state.lastPanorama) {
        container.classList.add('hidden');
        return;
    }
    const logo = document.createElement('img');
    logo.src = '/static/img/logo-mark.svg';
    logo.alt = '';
    const label = document.createElement('span');
    label.textContent = 'Осмотреть место';
    container.append(logo, label);
    container.classList.remove('hidden');
}

function disposePanoramaPlayer() {
    stopNoMoveWatchdog();
    if (state.panoramaPlayer) {
        try { state.panoramaPlayer.destroy(); } catch (error) { /* уже уничтожен */ }
        state.panoramaPlayer = null;
    }
    const container = document.getElementById('panorama-player');
    if (container) container.innerHTML = '';
}

export function destroyPanoramaPlayer() {
    state.roundLoadId++;
    disposePanoramaPlayer();
}

export async function openPanoModal() {
    if (!state.lastPanorama || typeof ymaps === 'undefined') return;
    const modal = document.getElementById('pano-modal');
    const container = document.getElementById('pano-modal-player');
    closePanoModal();
    const loadId = state.modalLoadId;
    const panorama = state.lastPanorama;
    container.innerHTML = '';
    modal.classList.remove('hidden');
    try {
        const player = await openPlayer(container, panorama, {
            controls: ['zoomControl'],
            suppressMapOpenBlock: true
        });
        if (loadId !== state.modalLoadId || modal.classList.contains('hidden')) {
            try { player.destroy(); } catch (destroyError) { /* уже закрыт */ }
            return;
        }
        state.modalPlayer = player;
    } catch (error) {
        if (loadId !== state.modalLoadId) return;
        console.error('Не удалось открыть панораму на весь экран:', error);
        modal.classList.add('hidden');
        showToast('Не удалось открыть панораму');
    }
}

export function closePanoModal() {
    state.modalLoadId++;
    const modal = document.getElementById('pano-modal');
    modal.classList.add('hidden');
    if (state.modalPlayer) {
        try { state.modalPlayer.destroy(); } catch (error) { /* уже уничтожен */ }
        state.modalPlayer = null;
    }
    const container = document.getElementById('pano-modal-player');
    if (container) container.innerHTML = '';
}

export function destroyResultPano() {
    const container = document.getElementById('result-pano');
    if (container) container.innerHTML = '';
}

export async function clientReverseGeocode(latitude, longitude) {
    try {
        await ymapsV2Ready();
        if (typeof ymaps.geocode !== 'function') return null;
        const result = await withTimeout(ymaps.geocode([latitude, longitude], {
            kind: 'house',
            results: 1
        }), 5000, 'Таймаут геокодера');
        const geoObject = result.geoObjects.get(0);
        return geoObject ? (geoObject.properties.get('name') || null) : null;
    } catch (error) {
        console.error('Клиентский геокодер недоступен:', error);
        return null;
    }
}
