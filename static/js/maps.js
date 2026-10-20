/**
 * Карты (JS API v3): карта выбора, карта результата раунда,
 * карта всех раундов на финале, мобильная панель-«лист».
 */
import { state, SPB_CENTER, DEFAULT_ZOOM, PIN_RED, PIN_NAVY } from './state.js';
import { createPinElement, TILE_SIZE, mercatorY, mercatorYInv } from './utils.js';
import { reloadFailedScript, withTimeout } from './sdk.js';

const API_READY_TIMEOUT_MS = 12000;
let v3ReadyPromise = null;

// Начальные виды подобраны по городской застройке и узнаваемым ориентирам.
// Это настройки камеры, а не границы игры и не веса генератора точек.
// Они не зависят от панорамы, пула или набора раундов и не меняются при
// раскрытии карты: увеличение панели просто открывает больше территории.
const DISTRICT_MAP_VIEWS = {
    admiralteysky:    { center: [30.301, 59.922], zoom: 12 }, // Коломна — Садовая
    vasileostrovsky:  { center: [30.251, 59.938], zoom: 12 }, // Линии Васильевского острова
    vyborgsky:        { center: [30.326, 60.025], zoom: 11 }, // Удельная — Озерки
    kalininsky:       { center: [30.393, 60.007], zoom: 11 }, // Академическая — Гражданка
    kirovsky:         { center: [30.266, 59.870], zoom: 11 }, // Автово и проспект Стачек
    kolpinsky:        { center: [30.592, 59.747], zoom: 11 }, // Колпино
    krasnogvardeysky: { center: [30.456, 59.963], zoom: 11 }, // Охта — Пороховые
    krasnoselsky:     { center: [30.163, 59.837], zoom: 11 }, // Жилая застройка юго-запада
    kronshtadtsky:    { center: [29.765, 59.997], zoom: 12 }, // Город Кронштадт на Котлине
    kurortny:         { center: [29.964, 60.098], zoom: 11 }, // Сестрорецк как начальный ориентир
    moskovsky:        { center: [30.328, 59.859], zoom: 11 }, // Московская — Парк Победы
    nevsky:          { center: [30.463, 59.900], zoom: 11 }, // Городские кварталы двух берегов
    petrogradsky:     { center: [30.293, 59.964], zoom: 12 }, // Петроградская сторона
    petrodvortsovy:   { center: [29.907, 59.880], zoom: 11 }, // Петергоф как начальный ориентир
    primorsky:        { center: [30.239, 60.021], zoom: 11 }, // Комендантский — озеро Долгое
    pushkinsky:       { center: [30.413, 59.707], zoom: 11 }, // Пушкин и Павловск
    frunzensky:       { center: [30.395, 59.869], zoom: 11 }, // Купчино
    tsentralny:       { center: [30.355, 59.933], zoom: 12 }, // Кварталы вокруг Невского
};

// Смена экранов сама вызывает :hover под неподвижным курсором. Разрешаем
// раскрытие нового раунда только после реального движения вне панели.
document.addEventListener('pointermove', event => {
    const panel = document.getElementById('map-panel');
    if (panel?.classList.contains('hover-paused') && !panel.contains(event.target)) {
        panel.classList.remove('hover-paused');
    }
});

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
                withTimeout(
                    ymaps3.ready, API_READY_TIMEOUT_MS, 'Таймаут готовности API карты'
                ).then(resolve, reject);
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
        window.yandexMapsLoadErrors = window.yandexMapsLoadErrors || {};
        window.yandexMapsLoadErrors.v3 = true;
        throw error;
    });
    return v3ReadyPromise;
}

/** Постоянный начальный вид территории, без привязки к ответу раунда. */
function initialGuessMapLocation() {
    const view = state.game.difficulty === 'district'
        ? DISTRICT_MAP_VIEWS[state.game.districtId] : null;
    if (view) return { center: [...view.center], zoom: view.zoom };
    return { center: [SPB_CENTER[1], SPB_CENTER[0]], zoom: DEFAULT_ZOOM };
}

/** Инициализация карты выбора. */
export async function initMap() {
    const loadId = ++state.mainMapLoadId;
    await ymapsV3Ready();
    if (loadId !== state.mainMapLoadId) return false;

    const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapListener } = ymaps3;

    const mapContainer = document.getElementById('map');
    disposeMainMap();
    mapContainer.innerHTML = '';

    state.map = new YMap(mapContainer, {
        location: initialGuessMapLocation()
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
function placeMarker(coordinates) {
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
function setMapPanelCollapsed(collapse) {
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
    const panel = document.getElementById('map-panel');
    panel.classList.add('hover-paused');
    // Не переносим focus-within от карты предыдущего раунда.
    if (panel.contains(document.activeElement)) document.activeElement.blur();
    // На телефоне раунд начинается со свёрнутой картой: первым делом игрок
    // всё равно осматривается, а карта закрывала бы пол-экрана.
    // На десктопе оставляем компактную панель; раскрытие — новым наведением.
    setMapPanelCollapsed(window.innerWidth <= 720);
    if (state.currentMarker && state.map) {
        state.map.removeChild(state.currentMarker);
        state.currentMarker = null;
    }
    state.guessCoords = null;
    document.getElementById('guess-btn').disabled = true;

    // Возвращаем подсказку. Пока карты нет, на месте остаётся кнопка её
    // повторной загрузки: без карты ответить нельзя.
    const hint = document.getElementById('map-hint');
    if (hint && state.map) {
        hint.textContent = 'Кликни по карте, чтобы отметить место';
        hint.classList.remove('hidden', 'map-retry');
        hint.onclick = null;
    }

    // Центрируем карту
    if (state.map) {
        state.map.setLocation(initialGuessMapLocation());
    }
}

/**
 * Размер карты результата и закрытые панелью полосы. На десктопе панель
 * закрывает правую часть, на мобильных — только скругление внизу.
 */
function resultMapFreeArea(mapContainer) {
    const rect = mapContainer.getBoundingClientRect();
    const isMobile = window.innerWidth <= 720;
    // Страховка: если контейнер ещё не получил размер, берём оценку от окна
    const width = rect.width > 50 ? rect.width : window.innerWidth;
    const height = rect.height > 50 ? rect.height
        : (isMobile ? window.innerHeight * 0.44 : window.innerHeight);
    const panelRight = isMobile ? 0 : Math.min(380, width);
    const panelBottom = isMobile
        ? Math.max(0, rect.bottom - document.querySelector('.result-panel').getBoundingClientRect().top)
        : 0;
    return { width, height, isMobile, panelRight, panelBottom };
}

/** Центр карты, при котором точка (lon, mercatorY) окажется в середине свободной части. */
function shiftedCenter(area, zoom, lon, mercY) {
    const worldPx = TILE_SIZE * Math.pow(2, zoom);
    return [
        lon + (area.panelRight / 2) * (360 / worldPx),
        mercatorYInv(mercY + (area.panelBottom / 2) / worldPx),
    ];
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
    const area = resultMapFreeArea(mapContainer);

    // Раунд без догадки (время вышло): только реальная точка — в середине
    // видимой части карты, как и пара точек в обычном раунде.
    if (!data.guess) {
        const zoom = 14;
        const [centerLon, centerLat] = shiftedCenter(area, zoom, correctLon, mercatorY(correctLat));
        state.resultMap = new YMap(mapContainer, {
            location: { center: [centerLon, centerLat], zoom }
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

    // Вертикальный запас больше горизонтального: пины рисуются НАД точкой
    // (~45px вверх) и на маленькой мобильной карте иначе срезаются краем
    const padX = area.isMobile ? 32 : 70;
    const padY = area.isMobile ? 72 : 70;

    // Минимальные охваты ~50 м: при точном попадании не упираемся в ноль,
    // а при маленьком промахе карта приближается к кварталу, не к городу
    const spanLon = Math.max(Math.abs(correctLon - guessLon), 0.0009) / 360;
    const spanY = Math.max(Math.abs(mercatorY(correctLat) - mercatorY(guessLat)), 0.0000012);
    const availW = Math.max(area.width - area.panelRight - padX * 2, 120);
    const availH = Math.max(area.height - area.panelBottom - padY * 2, 120);
    // Округляем вниз: дробный zoom карта округлит вверх, и точки выйдут за кадр
    const zoom = Math.floor(Math.max(3, Math.min(18,
        Math.min(
            Math.log2(availW / TILE_SIZE / spanLon),
            Math.log2(availH / TILE_SIZE / spanY)
        )
    )));
    const [centerLon, centerLat] = shiftedCenter(
        area, zoom,
        (correctLon + guessLon) / 2,
        (mercatorY(correctLat) + mercatorY(guessLat)) / 2,
    );

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
