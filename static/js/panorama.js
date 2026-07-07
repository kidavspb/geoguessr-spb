/**
 * Работа с панорамами Яндекса (JS API v2): поиск, плееры,
 * режим «без перемещения», предзагрузка, клиентский геокодер.
 */
import { state, MAX_PANORAMA_RETRIES } from './state.js';
import { showToast } from './utils.js';
import { api } from './api.js';

const PLAYER_OPTIONS = {
    // Без встроенной кнопки fullscreen: она пересоздавала панораму при
    // выходе, сбрасывая положение игрока. Карту прячет своя «ручка» панели.
    controls: ['zoomControl'],
    direction: [0, 0], // Начальное направление взгляда (азимут, наклон)
    span: [130, 80],   // Угол обзора
    suppressMapOpenBlock: true // Скрыть кнопку «Открыть в Яндекс.Картах»
};

/**
 * Дождаться загрузки JS API v2 (панорамы). Скрипт подключён в <head>,
 * но мог ещё не доехать по сети.
 */
export function ymapsV2Ready() {
    return new Promise((resolve, reject) => {
        if (typeof ymaps !== 'undefined' && ymaps.ready) {
            ymaps.ready(resolve);
            return;
        }
        let attempts = 0;
        const checkYmaps = setInterval(() => {
            attempts++;
            if (typeof ymaps !== 'undefined' && ymaps.ready) {
                clearInterval(checkYmaps);
                ymaps.ready(resolve);
            } else if (attempts > 50) {
                clearInterval(checkYmaps);
                reject(new Error('API v2 не загружен'));
            }
        }, 100);
    });
}

/**
 * Найти панораму: сначала в самой точке, затем по сетке смещений вокруг
 * (до ~200 м). Возвращает панораму или null.
 */
export async function locatePanorama(latitude, longitude) {
    const offsets = [
        [0, 0],
        [0.0005, 0], [-0.0005, 0], [0, 0.0005], [0, -0.0005],
        [0.001, 0], [-0.001, 0], [0, 0.001], [0, -0.001],
        [0.002, 0], [-0.002, 0], [0, 0.002], [0, -0.002],
        [0.0015, 0.0015], [-0.0015, 0.0015], [0.0015, -0.0015], [-0.0015, -0.0015]
    ];

    for (const [latOffset, lonOffset] of offsets) {
        try {
            const result = await ymaps.panorama.locate([latitude + latOffset, longitude + lonOffset]);
            if (result.length > 0) return result[0];
        } catch (e) {
            // точка без покрытия — пробуем следующее смещение
        }
    }
    return null;
}

/**
 * Загрузка панорамы раунда. Если панорамы нет и рядом — просит у сервера
 * новую точку (до лимита). preloaded — прогретый на экране результата
 * плеер: подключаем его мгновенно, без поиска и загрузки тайлов.
 */
export async function loadPanorama(latitude, longitude, preloaded = null) {
    const overlay = document.getElementById('photo-overlay');

    // Уничтожаем плеер прошлого раунда ДО очистки контейнера: иначе он
    // продолжает жить, опрашивает вырванный из DOM элемент и заваливает
    // консоль ошибками offsetWidth (заметно тормозит страницу).
    destroyPanoramaPlayer();
    const panoramaContainer = document.getElementById('panorama-player');
    panoramaContainer.innerHTML = '';

    // Прогретый плеер: переносим его контейнер внутрь игрового экрана —
    // WebGL-канвас переезжает вместе с уже загруженными тайлами
    if (preloaded && preloaded.player) {
        try {
            const div = preloaded.stagingDiv;
            div.removeAttribute('style');
            div.style.cssText = 'position:absolute; inset:0;';
            panoramaContainer.appendChild(div);

            state.panoramaPlayer = preloaded.player;
            state.lastPanorama = preloaded.panorama;
            const position = preloaded.panorama.getPosition();
            state.panoStartPoint = position.slice(0, 2);
            state.noMoveWarned = false;

            if (state.panoramaPlayer.fitToViewport) {
                state.panoramaPlayer.fitToViewport();
            }
            startNoMoveWatchdog();
            overlay.classList.add('hidden');
            return;
        } catch (error) {
            // прогрев не удался — обычный путь загрузки
            console.error('Прогретый плеер не подключился:', error);
            discardPreloaded({ player: preloaded.player, stagingDiv: preloaded.stagingDiv });
        }
    }

    try {
        await ymapsV2Ready();
    } catch (error) {
        console.error('Ошибка загрузки панорамы:', error);
        overlay.querySelector('span').textContent = 'Ошибка загрузки. Попробуйте перезагрузить страницу.';
        return;
    }

    const panorama = (preloaded && preloaded.panorama) ||
        await locatePanorama(latitude, longitude);

    if (!panorama) {
        // Панорамы нет — защищаемся от бесконечного цикла перегенераций
        if (state.panoramaRetries >= MAX_PANORAMA_RETRIES) {
            overlay.querySelector('span').textContent =
                'Не удалось найти панораму поблизости. Начните игру заново.';
            return;
        }
        state.panoramaRetries++;
        overlay.querySelector('span').textContent = 'Здесь нет панорамы, выбираем другое место...';

        const skipped = await api.skipLocation();
        if (skipped.ok && skipped.data) {
            await loadPanorama(skipped.data.latitude, skipped.data.longitude);
        } else {
            overlay.querySelector('span').textContent = 'Ошибка загрузки. Попробуйте перезагрузить страницу.';
        }
        return;
    }

    state.lastPanorama = panorama;

    // Реальные координаты панорамы — на сервер (по ним считаются очки)
    const position = panorama.getPosition();
    state.panoStartPoint = position.slice(0, 2); // для «к началу» и no-move
    state.noMoveWarned = false;
    await api.setActualPoint(position[0], position[1]);

    state.panoramaPlayer = new ymaps.panorama.Player(panoramaContainer, panorama, PLAYER_OPTIONS);
    startNoMoveWatchdog();

    // Прячем оверлей по факту загрузки (и по таймауту — на случай, если
    // событие не сработает)
    state.panoramaPlayer.events.add('panoramachange', () => overlay.classList.add('hidden'));
    setTimeout(() => overlay.classList.add('hidden'), 2000);
}

/**
 * Страховка режима «без перемещения»: событие panoramachange иногда
 * теряется при быстрых переходах, поэтому позицию дополнительно
 * сторожит интервал — любой уход от стартовой точки откатывается.
 */
function startNoMoveWatchdog() {
    stopNoMoveWatchdog();
    if (!state.gameData.noMove || !state.panoramaPlayer) return;

    state.panoramaPlayer.events.add('panoramachange', enforceNoMove);
    state.noMoveWatchdog = setInterval(enforceNoMove, 400);
}

function stopNoMoveWatchdog() {
    if (state.noMoveWatchdog) {
        clearInterval(state.noMoveWatchdog);
        state.noMoveWatchdog = null;
    }
}

/**
 * Откат перехода в режиме «без перемещения»
 */
function enforceNoMove() {
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
        // панорама в переходном состоянии — откатим на следующем тике
    }
}

/**
 * Предзагрузка следующего раунда с экрана результата: находим панораму
 * И создаём плеер в невидимом контейнере размером с экран — тайлы
 * загружаются, пока игрок изучает карту промаха. Сервер по ?peek=1
 * не запускает таймер раунда.
 */
export async function prefetchNextRound() {
    discardPreloaded(state.preloaded);
    state.preloaded = null;
    try {
        const location = await api.getLocation(true);
        if (!location.ok || !location.data || location.data.game_over) return;

        await ymapsV2Ready();
        const panorama = await locatePanorama(location.data.latitude, location.data.longitude);
        if (!panorama) return;

        // Координаты панорамы — на сервер уже сейчас (раунд на сервере
        // уже текущий, а клиенту потом не придётся ждать этот запрос)
        const position = panorama.getPosition();
        await api.setActualPoint(position[0], position[1]);

        // Невидимый контейнер во весь экран: плеер честно грузит тайлы
        const stagingDiv = document.createElement('div');
        stagingDiv.style.cssText =
            'position:fixed; inset:0; opacity:0; pointer-events:none; z-index:-1;';
        document.body.appendChild(stagingDiv);
        const player = new ymaps.panorama.Player(stagingDiv, panorama, PLAYER_OPTIONS);

        state.preloaded = {
            round: location.data.round,
            latitude: location.data.latitude,
            longitude: location.data.longitude,
            panorama,
            player,
            stagingDiv
        };
    } catch (error) {
        // предзагрузка — чистая оптимизация; не получилось — загрузим как обычно
    }
}

/**
 * Убрать неиспользованный прогретый плеер (смена раунда, конец игры)
 */
export function discardPreloaded(preloaded) {
    if (!preloaded) return;
    if (preloaded.player) {
        try {
            preloaded.player.destroy();
        } catch (error) {
            // уже уничтожен
        }
    }
    if (preloaded.stagingDiv && preloaded.stagingDiv.parentNode) {
        preloaded.stagingDiv.remove();
    }
}

/**
 * Вернуть панораму к стартовой точке раунда (если далеко ушёл по стрелкам)
 */
export async function returnToPanoStart() {
    if (!state.panoramaPlayer || !state.panoStartPoint) return;
    try {
        await state.panoramaPlayer.moveTo(state.panoStartPoint);
    } catch (error) {
        // moveTo мог не сработать (панорама пропала) — пересоздаём плеер
        console.error('Не удалось вернуться к началу:', error);
        if (state.lastPanorama) {
            const container = document.getElementById('panorama-player');
            destroyPanoramaPlayer();
            container.innerHTML = '';
            state.panoramaPlayer = new ymaps.panorama.Player(container, state.lastPanorama, PLAYER_OPTIONS);
        }
    }
}

/**
 * Мини-плеер с панорамой раунда в круглом окне панели результата:
 * к месту можно вернуться и осмотреться ещё раз
 */
export function showResultPano() {
    const container = document.getElementById('result-pano');
    destroyResultPano();
    container.innerHTML = '';

    if (!state.lastPanorama || typeof ymaps === 'undefined') {
        container.classList.add('hidden');
        return;
    }

    try {
        state.resultPanoPlayer = new ymaps.panorama.Player(container, state.lastPanorama, {
            controls: [],
            suppressMapOpenBlock: true
        });
        container.classList.remove('hidden');
    } catch (error) {
        console.error('Не удалось показать панораму в результате:', error);
        container.classList.add('hidden');
    }
}

/**
 * Уничтожить основной плеер панорамы (перед новым раундом / после игры)
 */
export function destroyPanoramaPlayer() {
    stopNoMoveWatchdog();
    if (state.panoramaPlayer) {
        try {
            state.panoramaPlayer.destroy();
        } catch (error) {
            // плеер мог уже умереть вместе с DOM-узлом
        }
        state.panoramaPlayer = null;
    }
}

/**
 * Полноэкранный просмотр панорамы раунда (клик по кругу на результате)
 */
export function openPanoModal() {
    if (!state.lastPanorama || typeof ymaps === 'undefined') return;

    const modal = document.getElementById('pano-modal');
    const container = document.getElementById('pano-modal-player');
    closePanoModal();
    container.innerHTML = '';
    modal.classList.remove('hidden');

    try {
        state.modalPlayer = new ymaps.panorama.Player(container, state.lastPanorama, {
            controls: ['zoomControl'],
            suppressMapOpenBlock: true
        });
    } catch (error) {
        console.error('Не удалось открыть панораму на весь экран:', error);
        modal.classList.add('hidden');
    }
}

export function closePanoModal() {
    const modal = document.getElementById('pano-modal');
    modal.classList.add('hidden');
    if (state.modalPlayer) {
        try {
            state.modalPlayer.destroy();
        } catch (error) {
            // уже уничтожен
        }
        state.modalPlayer = null;
    }
}

/**
 * Уничтожить мини-плеер при уходе с экрана результата
 */
export function destroyResultPano() {
    if (state.resultPanoPlayer) {
        try {
            state.resultPanoPlayer.destroy();
        } catch (error) {
            // плеер мог уже умереть вместе с DOM-узлом
        }
        state.resultPanoPlayer = null;
    }
}

/**
 * Обратное геокодирование на клиенте (JS API v2, ymaps.geocode).
 * Возвращает «улица, дом» или null — фолбэк, когда сервер без ключа геокодера.
 */
export async function clientReverseGeocode(latitude, longitude) {
    try {
        if (typeof ymaps === 'undefined' || typeof ymaps.geocode !== 'function') {
            return null;
        }
        const result = await ymaps.geocode([latitude, longitude], {
            kind: 'house',
            results: 1
        });
        const geoObject = result.geoObjects.get(0);
        return geoObject ? (geoObject.properties.get('name') || null) : null;
    } catch (error) {
        console.error('Клиентский геокодер недоступен:', error);
        return null;
    }
}
