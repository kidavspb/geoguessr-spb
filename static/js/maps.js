/**
 * Карты (JS API v3): карта выбора, карта результата раунда,
 * карта всех раундов на финале, мобильная панель-«лист».
 */
import { state, SPB_CENTER, DEFAULT_ZOOM, PIN_RED, PIN_NAVY } from './state.js';
import { createPinElement, TILE_SIZE, mercatorY, mercatorYInv } from './utils.js';
import { reloadFailedScript } from './sdk.js';

const API_READY_TIMEOUT_MS = 12000;
let v3ReadyPromise = null;

/** Дождаться async-скрипта API v3, не блокируя первый экран приложения. */
export function ymapsV3Ready() {
    if (v3ReadyPromise) return v3ReadyPromise;
    v3ReadyPromise = new Promise((resolve, reject) => {
        const started = performance.now();
        let scriptRetried = false;
        const check = () => {
            if (window.yandexMapsLoadErrors && window.yandexMapsLoadErrors.v3) {
                const reloading = !scriptRetried
                    ? reloadFailedScript('yandex-maps-v3', 'v3') : null;
                if (reloading) {
                    scriptRetried = true;
                    reloading.then(check, reject);
                    return;
                }
                reject(new Error('API карты не загрузился'));
                return;
            }
            if (typeof ymaps3 !== 'undefined' && ymaps3.ready) {
                Promise.resolve(ymaps3.ready).then(resolve, error => {
                    window.yandexMapsLoadErrors = window.yandexMapsLoadErrors || {};
                    window.yandexMapsLoadErrors.v3 = true;
                    reject(error);
                });
                return;
            }
            if (performance.now() - started >= API_READY_TIMEOUT_MS) {
                reject(new Error('Таймаут загрузки API карты'));
                return;
            }
            setTimeout(check, 100);
        };
        check();
    }).catch(error => {
        v3ReadyPromise = null;
        throw error;
    });
    return v3ReadyPromise;
}

/**
 * Инициализация карты выбора
 */
export async function initMap() {
    const loadId = ++state.mainMapLoadId;
    await ymapsV3Ready();
    if (loadId !== state.mainMapLoadId) return false;

    const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapListener } = ymaps3;

    const mapContainer = document.getElementById('map');
    disposeMainMap();
    mapContainer.innerHTML = '';

    state.map = new YMap(mapContainer, {
        location: {
            center: [SPB_CENTER[1], SPB_CENTER[0]], // [lon, lat]
            zoom: DEFAULT_ZOOM
        }
    });

    state.map.addChild(new YMapDefaultSchemeLayer());
    state.map.addChild(new YMapDefaultFeaturesLayer());

    // Обработчик клика по карте
    state.map.addChild(new YMapListener({
        layer: 'any',
        onClick: (object, event) => {
            if (event && event.coordinates) {
                placeMarker(event.coordinates);
            }
        }
    }));

    // Сброс маркера
    state.currentMarker = null;
    state.guessCoords = null;
    document.getElementById('guess-btn').disabled = true;
    return true;
}

function disposeMainMap() {
    if (state.map) {
        try {
            state.map.destroy();
        } catch (error) {
            // Заглушка тестов или уже потерянный DOM-контекст.
        }
        state.map = null;
        state.currentMarker = null;
    }
    const container = document.getElementById('map');
    if (container) container.innerHTML = '';
}

/** Уничтожить карту выбора и инвалидировать ещё не завершённую инициализацию. */
export function destroyMainMap() {
    state.mainMapLoadId++;
    disposeMainMap();
}

/**
 * Размещение маркера догадки на карте
 */
export function placeMarker(coordinates) {
    const [lon, lat] = coordinates;
    state.guessCoords = { latitude: lat, longitude: lon };

    if (state.currentMarker) {
        state.map.removeChild(state.currentMarker);
    }

    state.currentMarker = new ymaps3.YMapMarker({
        coordinates: [lon, lat]
    }, createPinElement(PIN_NAVY));

    state.map.addChild(state.currentMarker);

    // Прячем подсказку и активируем кнопку
    const hint = document.getElementById('map-hint');
    if (hint) hint.classList.add('hidden');
    document.getElementById('guess-btn').disabled =
        !state.roundInteractive || state.guessSubmitting;
}

/**
 * Свернуть/развернуть панель карты (мобильный «нижний лист»).
 * В отличие от встроенного fullscreen Яндекса, панораму не трогаем вообще —
 * игрок остаётся ровно там, куда дошёл.
 */
export function setMapPanelCollapsed(collapse) {
    const panel = document.getElementById('map-panel');
    const handle = document.getElementById('map-handle');
    panel.classList.toggle('collapsed', collapse);
    handle.textContent = collapse ? 'Открыть карту ▴' : 'Свернуть карту ▾';
}

export function toggleMapPanel() {
    const panel = document.getElementById('map-panel');
    setMapPanelCollapsed(!panel.classList.contains('collapsed'));
}

/**
 * Уничтожить карты результата/финала: каждая держит WebGL-контекст,
 * а на iOS их лимит быстро приводит к вылету вкладки
 */
function disposeResultMap() {
    if (state.resultMap) {
        try {
            state.resultMap.destroy();
        } catch (error) {
            // уже уничтожена
        }
        state.resultMap = null;
    }
    const container = document.getElementById('result-map');
    if (container) container.innerHTML = '';
}

export function destroyResultMap() {
    state.resultMapLoadId++;
    disposeResultMap();
}

function disposeFinalMap() {
    if (state.finalMap) {
        try {
            state.finalMap.destroy();
        } catch (error) {
            // уже уничтожена
        }
        state.finalMap = null;
    }
    const container = document.getElementById('final-map');
    if (container) container.innerHTML = '';
}

export function destroyFinalMap() {
    state.finalMapLoadId++;
    disposeFinalMap();
}

/**
 * Сброс карты для нового раунда
 */
export function resetMapForNewRound() {
    // На телефоне раунд начинается со свёрнутой картой: первым делом игрок
    // всё равно осматривается, а карта закрывала бы пол-экрана.
    // На десктопе панель маленькая и разворачивается наведением — оставляем.
    setMapPanelCollapsed(window.innerWidth <= 720);
    if (state.currentMarker && state.map) {
        state.map.removeChild(state.currentMarker);
        state.currentMarker = null;
    }
    state.guessCoords = null;
    document.getElementById('guess-btn').disabled = true;

    // Возвращаем подсказку
    const hint = document.getElementById('map-hint');
    if (hint) {
        hint.textContent = 'Кликни по карте, чтобы отметить место';
        hint.classList.remove('hidden', 'map-retry');
        hint.onclick = null;
    }

    // Центрируем карту
    if (state.map) {
        state.map.setLocation({
            center: [SPB_CENTER[1], SPB_CENTER[0]],
            zoom: DEFAULT_ZOOM
        });
    }
}

/**
 * Карта результата раунда: догадка, реальная точка, пунктир между ними
 */
export async function showResultMap(data) {
    const loadId = ++state.resultMapLoadId;
    disposeResultMap();
    await ymapsV3Ready();
    if (loadId !== state.resultMapLoadId ||
            state.resultRoundId !== data.round_id ||
            !document.getElementById('result-screen').classList.contains('active')) {
        return false;
    }

    const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapMarker, YMapFeature } = ymaps3;

    const mapContainer = document.getElementById('result-map');
    mapContainer.innerHTML = '';

    const correctLon = data.correct_location.longitude;
    const correctLat = data.correct_location.latitude;

    // Раунд без догадки (время вышло): показываем только реальную точку
    if (!data.guess) {
        state.resultMap = new YMap(mapContainer, {
            location: { center: [correctLon, correctLat], zoom: 14 }
        });
        state.resultMap.addChild(new YMapDefaultSchemeLayer());
        state.resultMap.addChild(new YMapDefaultFeaturesLayer());
        state.resultMap.addChild(new YMapMarker({
            coordinates: [correctLon, correctLat]
        }, createPinElement(PIN_RED)));
        return true;
    }

    const guessLon = data.guess.longitude;
    const guessLat = data.guess.latitude;

    // Сами вписываем обе точки в кадр — по фактическому размеру контейнера.
    // На мобильных контейнер карты уже обрезан по высоте панели (CSS),
    // на десктопе панель закрывает правую часть — учитываем её ширину.
    const rect = mapContainer.getBoundingClientRect();
    const isMobile = window.innerWidth <= 720;
    // Страховка: если контейнер ещё не получил размер, берём оценку от окна
    const stageW = rect.width > 50 ? rect.width : window.innerWidth;
    const stageH = rect.height > 50 ? rect.height
        : (isMobile ? window.innerHeight * 0.44 : window.innerHeight);
    const panelRight = isMobile ? 0 : Math.min(380, stageW);
    const panelBottom = 0;
    // Вертикальный запас больше горизонтального: пины рисуются НАД точкой
    // (~45px вверх) и на маленькой мобильной карте иначе срезаются краем
    const padX = isMobile ? 32 : 70;
    const padY = isMobile ? 72 : 70;

    // Минимальные охваты ~50 м: при точном попадании не упираемся в ноль,
    // а при маленьком промахе карта приближается к кварталу, не к городу
    const spanLon = Math.max(Math.abs(correctLon - guessLon), 0.0009) / 360;
    const spanY = Math.max(Math.abs(mercatorY(correctLat) - mercatorY(guessLat)), 0.0000012);
    const availW = Math.max(stageW - panelRight - padX * 2, 120);
    const availH = Math.max(stageH - panelBottom - padY * 2, 120);
    // Округляем вниз: дробный zoom карта округлит вверх, и точки выйдут за кадр
    const zoom = Math.floor(Math.max(3, Math.min(18,
        Math.min(
            Math.log2(availW / TILE_SIZE / spanLon),
            Math.log2(availH / TILE_SIZE / spanY)
        )
    )));

    // Геометрический центр двух точек, сдвинутый так, чтобы он оказался
    // в середине свободной от панели области
    const worldPx = TILE_SIZE * Math.pow(2, zoom);
    const centerLon = (correctLon + guessLon) / 2 + (panelRight / 2) * (360 / worldPx);
    const centerY = (mercatorY(correctLat) + mercatorY(guessLat)) / 2 + (panelBottom / 2) / worldPx;
    const centerLat = mercatorYInv(centerY);

    state.resultMap = new YMap(mapContainer, {
        location: {
            center: [centerLon, centerLat],
            zoom
        }
    });

    state.resultMap.addChild(new YMapDefaultSchemeLayer());
    state.resultMap.addChild(new YMapDefaultFeaturesLayer());

    // Пунктирная линия между догадкой и реальной точкой
    state.resultMap.addChild(new YMapFeature({
        geometry: {
            type: 'LineString',
            coordinates: [
                [guessLon, guessLat],
                [correctLon, correctLat]
            ]
        },
        style: {
            stroke: [{ color: '#231c62', width: 3, dash: [6, 6] }]
        }
    }));

    // Реальная точка — фирменный красный пин, догадка — синий
    state.resultMap.addChild(new YMapMarker({
        coordinates: [correctLon, correctLat]
    }, createPinElement(PIN_RED)));
    state.resultMap.addChild(new YMapMarker({
        coordinates: [guessLon, guessLat]
    }, createPinElement(PIN_NAVY)));
    return true;
}

/**
 * Карта всех раундов игры на финальном экране:
 * синие пины — догадки, красные — реальные точки, пунктир — промахи.
 */
export async function renderFinalMap(rounds) {
    const container = document.getElementById('final-map');
    const points = [];
    rounds.forEach(r => {
        if (r.actual) points.push([r.actual.longitude, r.actual.latitude]);
        if (r.guess) points.push([r.guess.longitude, r.guess.latitude]);
    });

    if (points.length === 0) {
        destroyFinalMap();
        container.classList.add('hidden');
        return;
    }
    const loadId = ++state.finalMapLoadId;
    disposeFinalMap();
    container.classList.remove('hidden');
    container.innerHTML = '';

    try {
        await ymapsV3Ready();
        if (loadId !== state.finalMapLoadId ||
                !document.getElementById('final-screen').classList.contains('active')) {
            return false;
        }
        const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapMarker, YMapFeature } = ymaps3;

        // Вписываем все точки в контейнер (той же меркаторской математикой,
        // что и карта результата раунда)
        const lons = points.map(p => p[0]);
        const ys = points.map(p => mercatorY(p[1]));
        const spanLon = Math.max(Math.max(...lons) - Math.min(...lons), 0.002) / 360;
        const spanY = Math.max(Math.max(...ys) - Math.min(...ys), 0.000003);
        const width = container.clientWidth || 400;
        const height = container.clientHeight || 280;
        const pad = 36;
        const zoom = Math.floor(Math.max(3, Math.min(16,
            Math.min(
                Math.log2((width - pad * 2) / TILE_SIZE / spanLon),
                Math.log2((height - pad * 2) / TILE_SIZE / spanY)
            )
        )));
        const centerLon = (Math.max(...lons) + Math.min(...lons)) / 2;
        const centerLat = mercatorYInv((Math.max(...ys) + Math.min(...ys)) / 2);

        state.finalMap = new YMap(container, {
            location: { center: [centerLon, centerLat], zoom }
        });
        state.finalMap.addChild(new YMapDefaultSchemeLayer());
        state.finalMap.addChild(new YMapDefaultFeaturesLayer());

        rounds.forEach(r => {
            if (r.guess && r.actual) {
                state.finalMap.addChild(new YMapFeature({
                    geometry: {
                        type: 'LineString',
                        coordinates: [
                            [r.guess.longitude, r.guess.latitude],
                            [r.actual.longitude, r.actual.latitude]
                        ]
                    },
                    style: { stroke: [{ color: '#231c62', width: 2, dash: [5, 5] }] }
                }));
            }
            if (r.actual) {
                state.finalMap.addChild(new YMapMarker({
                    coordinates: [r.actual.longitude, r.actual.latitude]
                }, createPinElement(PIN_RED)));
            }
            if (r.guess) {
                state.finalMap.addChild(new YMapMarker({
                    coordinates: [r.guess.longitude, r.guess.latitude]
                }, createPinElement(PIN_NAVY)));
            }
        });
        return true;
    } catch (error) {
        console.error('Не удалось построить карту раундов:', error);
        if (loadId === state.finalMapLoadId) container.classList.add('hidden');
        return false;
    }
}
