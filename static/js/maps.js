/**
 * Карты (JS API v3): карта выбора, карта результата раунда,
 * карта всех раундов на финале, мобильная панель-«лист».
 */
import { state, SPB_CENTER, DEFAULT_ZOOM, PIN_RED, PIN_NAVY } from './state.js';
import { createPinElement, TILE_SIZE, mercatorY, mercatorYInv } from './utils.js';

/**
 * Инициализация карты выбора
 */
export async function initMap() {
    if (typeof ymaps3 === 'undefined') {
        throw new Error('Яндекс.Карты API не загружен. Проверьте подключение к интернету.');
    }

    await ymaps3.ready;

    const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapListener } = ymaps3;

    const mapContainer = document.getElementById('map');
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
    document.getElementById('guess-btn').disabled = false;
}

/**
 * Свернуть/развернуть панель карты (мобильный «нижний лист»).
 * В отличие от встроенного fullscreen Яндекса, панораму не трогаем вообще —
 * игрок остаётся ровно там, куда дошёл.
 */
export function toggleMapPanel(forceExpand = false) {
    const panel = document.getElementById('map-panel');
    const handle = document.getElementById('map-handle');
    const collapse = forceExpand === true ? false : !panel.classList.contains('collapsed');
    panel.classList.toggle('collapsed', collapse);
    handle.textContent = collapse ? 'Открыть карту ▴' : 'Свернуть карту ▾';
}

/**
 * Сброс карты для нового раунда
 */
export function resetMapForNewRound() {
    // Разворачиваем панель карты, если её свернули в прошлом раунде
    toggleMapPanel(true);
    if (state.currentMarker && state.map) {
        state.map.removeChild(state.currentMarker);
        state.currentMarker = null;
    }
    state.guessCoords = null;
    document.getElementById('guess-btn').disabled = true;

    // Возвращаем подсказку
    const hint = document.getElementById('map-hint');
    if (hint) hint.classList.remove('hidden');

    // Центрируем карту
    if (state.map) {
        state.map.setLocation({
            center: [SPB_CENTER[1], SPB_CENTER[0]],
            zoom: DEFAULT_ZOOM,
            duration: 500
        });
    }
}

/**
 * Карта результата раунда: догадка, реальная точка, пунктир между ними
 */
export async function showResultMap(data) {
    await ymaps3.ready;

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
        return;
    }

    const guessLon = data.guess.longitude;
    const guessLat = data.guess.latitude;

    // Сами вписываем обе точки в кадр — по фактическому размеру контейнера.
    // На мобильных контейнер карты уже обрезан по высоте панели (CSS),
    // на десктопе панель закрывает правую часть — учитываем её ширину.
    const rect = mapContainer.getBoundingClientRect();
    const stageW = rect.width || window.innerWidth;
    const stageH = rect.height || window.innerHeight;
    const isMobile = window.innerWidth <= 720;
    const panelRight = isMobile ? 0 : Math.min(380, stageW);
    const panelBottom = 0;
    const padPx = isMobile ? 40 : 70;

    // Минимальные охваты ~50 м: при точном попадании не упираемся в ноль,
    // а при маленьком промахе карта приближается к кварталу, не к городу
    const spanLon = Math.max(Math.abs(correctLon - guessLon), 0.0009) / 360;
    const spanY = Math.max(Math.abs(mercatorY(correctLat) - mercatorY(guessLat)), 0.0000012);
    const availW = Math.max(stageW - panelRight - padPx * 2, 200);
    const availH = Math.max(stageH - panelBottom - padPx * 2, 200);
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

    if (points.length === 0 || typeof ymaps3 === 'undefined') {
        container.classList.add('hidden');
        return;
    }
    container.classList.remove('hidden');
    container.innerHTML = '';

    try {
        await ymaps3.ready;
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
    } catch (error) {
        console.error('Не удалось построить карту раундов:', error);
        container.classList.add('hidden');
    }
}
