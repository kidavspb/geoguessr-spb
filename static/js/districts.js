/**
 * Выбор административного района на локальной SVG-карте.
 *
 * GeoJSON остаётся единственной геометрией: этот модуль только проецирует его
 * координаты в SVG path. Native select дублирует не карту, а доступный способ
 * выбора — он работает даже при ошибке загрузки geometry.
 */
import { showScreen } from './utils.js';

const GEOMETRY_URL = '/api/districts/geometry';
const MAP_WIDTH = 1000;
const MAP_HEIGHT = 680;
const FULL_VIEWBOX = `0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`;
const EXPECTED_DISTRICT_COUNT = 18;
const GEOMETRY_TIMEOUT_MS = 8000;
const MAX_ZOOM = 4.5;
const FIT_PADDING_RATIO = 0.035;
const MIN_FIT_PADDING = 18;
const PAN_THRESHOLD_PX = 5;
const CLICK_SUPPRESSION_MS = 250;
const MIN_VISIBLE_EXTENT = 0.2;

let initialized = false;
let geometryPromise = null;
let geometryRendered = false;
let draftDistrictId = null;
let confirmSelection = null;
let pickerOpenId = 0;
let geometryBounds = null;
let camera = null;
let resizeObserver = null;
const activePointers = new Map();
let panGesture = null;
let pinchGesture = null;
let touchMapOwned = false;
let suppressMapClicksUntil = 0;

function districtOptions() {
    const select = document.getElementById('district-list');
    if (!select) return [];
    return Array.from(select.options)
        .filter(option => option.value)
        .map(option => ({ id: option.value, name: option.textContent.trim() }));
}

function districtName(districtId) {
    if (!districtId) return null;
    const option = document.querySelector(
        `#district-list option[value="${CSS.escape(districtId)}"]`
    );
    return option ? option.textContent.trim() : null;
}

function updateSelectionUi() {
    const selectedName = districtName(draftDistrictId);
    const select = document.getElementById('district-list');
    const output = document.getElementById('district-selected-name');
    const confirm = document.getElementById('district-confirm-btn');

    if (select) select.value = selectedName ? draftDistrictId : '';
    if (output) output.textContent = selectedName || 'Район не выбран';
    if (confirm) confirm.disabled = !selectedName;

    const paths = Array.from(document.querySelectorAll(
        '#district-map .district-shape'
    ));
    paths.forEach((path, index) => {
        const selected = path.dataset.districtId === draftDistrictId;
        path.classList.toggle('is-selected', selected);
        path.setAttribute('aria-checked', String(selected));
        // ARIA radio group должна быть одной остановкой Tab. После выбора
        // фокусной становится активная геометрия; без выбора — первый район.
        path.setAttribute('tabindex', String(selected || (!selectedName && index === 0)
            ? 0 : -1));
    });
}

function setDraftDistrict(districtId) {
    draftDistrictId = districtName(districtId) ? districtId : null;
    updateSelectionUi();
}

function visitCoordinates(geometry, callback) {
    if (!geometry || !Array.isArray(geometry.coordinates)) {
        throw new Error('У района отсутствует geometry');
    }
    const polygons = geometry.type === 'Polygon'
        ? [geometry.coordinates]
        : geometry.type === 'MultiPolygon'
            ? geometry.coordinates
            : null;
    if (!polygons) throw new Error(`Неподдерживаемая geometry: ${geometry.type}`);

    polygons.forEach(polygon => polygon.forEach(ring => ring.forEach(coord => {
        if (!Array.isArray(coord) || coord.length < 2 ||
                !Number.isFinite(coord[0]) || !Number.isFinite(coord[1])) {
            throw new Error('Некорректная координата района');
        }
        callback(coord[0], coord[1]);
    })));
}

function projectedBounds(features) {
    let minLon = Infinity;
    let minLat = Infinity;
    let maxLon = -Infinity;
    let maxLat = -Infinity;
    features.forEach(feature => visitCoordinates(feature.geometry, (lon, lat) => {
        minLon = Math.min(minLon, lon);
        maxLon = Math.max(maxLon, lon);
        minLat = Math.min(minLat, lat);
        maxLat = Math.max(maxLat, lat);
    }));
    if (![minLon, minLat, maxLon, maxLat].every(Number.isFinite) ||
            minLon >= maxLon || minLat >= maxLat) {
        throw new Error('Пустые или вырожденные границы районов');
    }

    // На широте Петербурга градус долготы примерно вдвое короче градуса
    // широты. Эта локальная equirectangular projection нужна только для UI.
    const lonScale = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
    const projectedWidth = (maxLon - minLon) * lonScale;
    const projectedHeight = maxLat - minLat;
    const padding = 28;
    const scale = Math.min(
        (MAP_WIDTH - padding * 2) / projectedWidth,
        (MAP_HEIGHT - padding * 2) / projectedHeight,
    );
    const contentWidth = projectedWidth * scale;
    const contentHeight = projectedHeight * scale;
    const offsetX = (MAP_WIDTH - contentWidth) / 2;
    const offsetY = (MAP_HEIGHT - contentHeight) / 2;

    return {
        project(lon, lat) {
            return [
                offsetX + (lon - minLon) * lonScale * scale,
                offsetY + (maxLat - lat) * scale,
            ];
        },
    };
}

function featurePath(feature, project, pointCollector) {
    const polygons = feature.geometry.type === 'Polygon'
        ? [feature.geometry.coordinates]
        : feature.geometry.coordinates;
    return polygons.map(polygon => polygon.map(ring => {
        if (!ring.length) return '';
        const points = ring.map(([lon, lat]) => {
            const point = project(lon, lat);
            pointCollector(point);
            return `${point[0].toFixed(1)} ${point[1].toFixed(1)}`;
        });
        return `M${points.join('L')}Z`;
    }).join('')).join('');
}

function pointsBounds(points) {
    if (!points.length) return null;
    const xs = points.map(point => point[0]);
    const ys = points.map(point => point[1]);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

function copyViewBox(box) {
    return { x: box.x, y: box.y, width: box.width, height: box.height };
}

function viewBoxValue(box) {
    return `${box.x.toFixed(2)} ${box.y.toFixed(2)} ` +
        `${box.width.toFixed(2)} ${box.height.toFixed(2)}`;
}

/**
 * Начальный viewport следует не условному холсту 1000x680, а реальному bbox
 * всех районов. Дополнительное расширение короткой оси до aspect ratio DOM
 * убирает второй слой letterbox, сохраняя всю разорванную геометрию города.
 */
function fittedViewBox(svg) {
    if (!geometryBounds) return null;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;

    const padding = Math.max(
        MIN_FIT_PADDING,
        Math.min(geometryBounds.width, geometryBounds.height) * FIT_PADDING_RATIO,
    );
    const box = {
        x: geometryBounds.x - padding,
        y: geometryBounds.y - padding,
        width: geometryBounds.width + padding * 2,
        height: geometryBounds.height + padding * 2,
    };
    const viewportAspect = rect.width / rect.height;
    const boxAspect = box.width / box.height;
    if (boxAspect < viewportAspect) {
        const expandedWidth = box.height * viewportAspect;
        box.x -= (expandedWidth - box.width) / 2;
        box.width = expandedWidth;
    } else if (boxAspect > viewportAspect) {
        const expandedHeight = box.width / viewportAspect;
        box.y -= (expandedHeight - box.height) / 2;
        box.height = expandedHeight;
    }
    return box;
}

function mapIsZoomed() {
    return Boolean(camera && camera.zoom > 1.001);
}

function updateCameraUi() {
    const svg = document.getElementById('district-map');
    const reset = document.getElementById('district-map-reset');
    const zoomed = mapIsZoomed();
    svg?.classList.toggle('is-zoomed', zoomed);
    if (!zoomed) svg?.classList.remove('is-panning');
    reset?.classList.toggle('hidden', !zoomed);
}

function applyCamera() {
    const svg = document.getElementById('district-map');
    if (!svg || !camera) return;
    svg.setAttribute('viewBox', viewBoxValue(camera.view));
    updateCameraUi();
}

function resetMapView() {
    const svg = document.getElementById('district-map');
    if (!svg || !geometryRendered) return false;
    const fit = fittedViewBox(svg);
    if (!fit) return false;
    camera = { base: fit, view: copyViewBox(fit), zoom: 1 };
    applyCamera();
    return true;
}

function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
}

function boundedViewBox(box) {
    const base = camera.base;
    const width = Math.min(base.width, box.width);
    const height = Math.min(base.height, box.height);
    const visibleWidth = Math.min(width * MIN_VISIBLE_EXTENT, geometryBounds.width);
    const visibleHeight = Math.min(height * MIN_VISIBLE_EXTENT, geometryBounds.height);
    const minimumX = Math.max(base.x, geometryBounds.x - width + visibleWidth);
    const maximumX = Math.min(
        base.x + base.width - width,
        geometryBounds.x + geometryBounds.width - visibleWidth,
    );
    const minimumY = Math.max(base.y, geometryBounds.y - height + visibleHeight);
    const maximumY = Math.min(
        base.y + base.height - height,
        geometryBounds.y + geometryBounds.height - visibleHeight,
    );
    return {
        x: clamp(box.x, minimumX, maximumX),
        y: clamp(box.y, minimumY, maximumY),
        width,
        height,
    };
}

function zoomAt(clientX, clientY, requestedZoom) {
    const svg = document.getElementById('district-map');
    if (!svg || !camera) return false;
    const nextZoom = clamp(requestedZoom, 1, MAX_ZOOM);
    if (Math.abs(nextZoom - camera.zoom) < 0.0001) return false;

    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const screenX = clamp((clientX - rect.left) / rect.width, 0, 1);
    const screenY = clamp((clientY - rect.top) / rect.height, 0, 1);
    const anchorX = camera.view.x + screenX * camera.view.width;
    const anchorY = camera.view.y + screenY * camera.view.height;
    const width = camera.base.width / nextZoom;
    const height = camera.base.height / nextZoom;

    camera.view = boundedViewBox({
        x: anchorX - screenX * width,
        y: anchorY - screenY * height,
        width,
        height,
    });
    camera.zoom = nextZoom;
    applyCamera();
    return true;
}

function panByPixels(deltaX, deltaY) {
    const svg = document.getElementById('district-map');
    if (!svg || !camera || !mapIsZoomed()) return;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    camera.view = boundedViewBox({
        x: camera.view.x - deltaX * camera.view.width / rect.width,
        y: camera.view.y - deltaY * camera.view.height / rect.height,
        width: camera.view.width,
        height: camera.view.height,
    });
    applyCamera();
}

function pointerDistance(first, second) {
    return Math.hypot(second.x - first.x, second.y - first.y);
}

function pointerMidpoint(first, second) {
    return { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
}

function capturePointer(svg, pointerId) {
    try {
        svg.setPointerCapture(pointerId);
    } catch (error) {
        // Pointer мог завершиться между двумя событиями pinch — это безопасно.
    }
}

function beginPinch(svg, capture = true) {
    const pointers = Array.from(activePointers.values()).slice(0, 2);
    if (pointers.length < 2) return;
    const distance = pointerDistance(pointers[0], pointers[1]);
    if (distance <= 0) return;
    const midpoint = pointerMidpoint(pointers[0], pointers[1]);
    pinchGesture = {
        pointerIds: pointers.map(pointer => pointer.id),
        previousDistance: distance,
        previousMidpoint: midpoint,
        startDistance: distance,
        startMidpoint: midpoint,
        moved: false,
    };
    panGesture = null;
    if (capture) pinchGesture.pointerIds.forEach(pointerId => capturePointer(svg, pointerId));
}

function beginPan(pointer) {
    panGesture = {
        pointerId: pointer.id,
        startX: pointer.x,
        startY: pointer.y,
        previousX: pointer.x,
        previousY: pointer.y,
        moved: false,
    };
}

function handleMapPointerDown(event) {
    if (!camera || (event.pointerType === 'mouse' && event.button !== 0)) return;
    const svg = event.currentTarget;
    activePointers.set(event.pointerId, {
        id: event.pointerId,
        x: event.clientX,
        y: event.clientY,
    });
    if (activePointers.size >= 2) {
        event.preventDefault();
        beginPinch(svg, !event.touchDriven);
    } else {
        // В fit это только tap-vs-scroll detector: движение страницы не
        // перехватываем, но и click после свайпа не считаем выбором района.
        beginPan(activePointers.get(event.pointerId));
    }
}

function handleMapPointerMove(event) {
    if (!activePointers.has(event.pointerId) || !camera) return;
    activePointers.set(event.pointerId, {
        id: event.pointerId,
        x: event.clientX,
        y: event.clientY,
    });

    if (pinchGesture) {
        const pointers = pinchGesture.pointerIds.map(id => activePointers.get(id));
        if (pointers.some(pointer => !pointer)) return;
        event.preventDefault();
        const distance = pointerDistance(pointers[0], pointers[1]);
        const midpoint = pointerMidpoint(pointers[0], pointers[1]);
        if (distance > 0 && pinchGesture.previousDistance > 0) {
            zoomAt(
                midpoint.x,
                midpoint.y,
                camera.zoom * distance / pinchGesture.previousDistance,
            );
            panByPixels(
                midpoint.x - pinchGesture.previousMidpoint.x,
                midpoint.y - pinchGesture.previousMidpoint.y,
            );
        }
        if (Math.abs(distance - pinchGesture.startDistance) >= PAN_THRESHOLD_PX ||
                Math.hypot(
                    midpoint.x - pinchGesture.startMidpoint.x,
                    midpoint.y - pinchGesture.startMidpoint.y,
                ) >= PAN_THRESHOLD_PX) {
            pinchGesture.moved = true;
        }
        pinchGesture.previousDistance = distance;
        pinchGesture.previousMidpoint = midpoint;
        return;
    }

    if (!panGesture || panGesture.pointerId !== event.pointerId) return;
    const totalDistance = Math.hypot(
        event.clientX - panGesture.startX,
        event.clientY - panGesture.startY,
    );
    if (!mapIsZoomed()) {
        if (totalDistance >= PAN_THRESHOLD_PX) panGesture.moved = true;
        panGesture.previousX = event.clientX;
        panGesture.previousY = event.clientY;
        return;
    }
    if (!panGesture.moved && totalDistance >= PAN_THRESHOLD_PX) {
        panGesture.moved = true;
        if (!event.touchDriven) capturePointer(event.currentTarget, event.pointerId);
        event.currentTarget.classList.add('is-panning');
    }
    if (panGesture.moved) {
        event.preventDefault();
        panByPixels(
            event.clientX - panGesture.previousX,
            event.clientY - panGesture.previousY,
        );
    }
    panGesture.previousX = event.clientX;
    panGesture.previousY = event.clientY;
}

function suppressSyntheticMapClick() {
    suppressMapClicksUntil = performance.now() + CLICK_SUPPRESSION_MS;
}

function handleMapPointerEnd(event) {
    if (!activePointers.has(event.pointerId)) return;
    const pinchMoved = Boolean(pinchGesture && pinchGesture.moved);
    const panMoved = Boolean(panGesture && panGesture.moved &&
        panGesture.pointerId === event.pointerId);
    activePointers.delete(event.pointerId);

    if (pinchGesture && pinchGesture.pointerIds.includes(event.pointerId)) {
        if (pinchMoved) suppressSyntheticMapClick();
        pinchGesture = null;
        const remaining = activePointers.values().next().value;
        if (remaining && mapIsZoomed()) beginPan(remaining);
    } else if (panGesture && panGesture.pointerId === event.pointerId) {
        if (panMoved) suppressSyntheticMapClick();
        panGesture = null;
    }
    document.getElementById('district-map')?.classList.remove('is-panning');
}

function handleMapClickCapture(event) {
    // Клавиатурный click (detail=0) всегда должен доходить до radio-path.
    if (event.detail !== 0 && performance.now() < suppressMapClicksUntil) {
        event.preventDefault();
        event.stopImmediatePropagation();
    }
}

function handleMapWheel(event) {
    if (!camera) return;
    const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE
        ? 16 : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
            ? event.currentTarget.clientHeight : 1;
    const delta = event.deltaY * unit;
    const requestedZoom = camera.zoom * Math.exp(-delta * 0.0018);
    const nextZoom = clamp(requestedZoom, 1, MAX_ZOOM);

    // В исходном fit прокрутка вниз остаётся обычным scroll страницы.
    if (camera.zoom <= 1.001 && nextZoom <= 1.001) return;
    event.preventDefault();
    zoomAt(event.clientX, event.clientY, nextZoom);
}

/** Touch Events сохраняются в Safari даже после pointercancel при scroll.
 * Перехватываем pinch на втором touchstart, а не сменой touch-action посреди
 * жеста. Один палец в fit остаётся нативной прокруткой страницы.
 */
function handleMapTouch(event) {
    if (!camera) return;
    const svg = event.currentTarget;
    // Пальцы могут лежать на разных SVG path: targetTouches тогда содержит
    // только один из них. Учитываем все касания, начавшиеся внутри карты.
    const touches = Array.from(event.touches).filter(touch => svg.contains(touch.target));
    const adapter = touch => ({
        pointerId: touch.identifier, clientX: touch.clientX, clientY: touch.clientY,
        currentTarget: svg, touchDriven: true,
        preventDefault: () => { if (event.cancelable) event.preventDefault(); },
    });
    if (event.type === 'touchstart') {
        if (touches.length >= 2 || mapIsZoomed()) touchMapOwned = true;
        // Не отменяем одиночный touchstart: обычный tap должен дать click.
        if (touches.length >= 2 && event.cancelable) event.preventDefault();
        for (const touch of touches) {
            if (!activePointers.has(touch.identifier)) handleMapPointerDown(adapter(touch));
        }
    } else if (event.type === 'touchmove') {
        if (touchMapOwned && event.cancelable) event.preventDefault();
        // Обе позиции обновляем атомарно: один touchmove — один шаг камеры.
        for (const touch of touches) {
            if (activePointers.has(touch.identifier)) {
                activePointers.set(touch.identifier, {
                    id: touch.identifier, x: touch.clientX, y: touch.clientY,
                });
            }
        }
        const tracked = touches.find(touch => activePointers.has(touch.identifier));
        if (tracked) handleMapPointerMove(adapter(tracked));
    } else {
        if (touchMapOwned && (pinchGesture || panGesture?.moved)) suppressSyntheticMapClick();
        for (const touch of Array.from(event.changedTouches)) {
            handleMapPointerEnd(adapter(touch));
        }
        if (event.type === 'touchcancel' || touches.length === 0) {
            activePointers.clear();
            pinchGesture = null;
            panGesture = null;
            touchMapOwned = false;
        }
    }
}

function initMapInteractions(svg, reset) {
    // Touch не дублируем через Pointer Events: Safari может отменить только
    // pointer-поток, пока пальцы всё ещё касаются экрана.
    const nonTouch = handler => event => {
        if (event.pointerType !== 'touch') handler(event);
    };
    svg.addEventListener('pointerdown', nonTouch(handleMapPointerDown));
    svg.addEventListener('pointermove', nonTouch(handleMapPointerMove));
    for (const type of ['touchstart', 'touchmove', 'touchend', 'touchcancel']) {
        svg.addEventListener(type, handleMapTouch, { passive: false });
    }
    svg.addEventListener('click', handleMapClickCapture, true);
    svg.addEventListener('wheel', handleMapWheel, { passive: false });
    window.addEventListener('pointerup', nonTouch(handleMapPointerEnd));
    window.addEventListener('pointercancel', nonTouch(handleMapPointerEnd));
    reset.addEventListener('click', event => {
        const focusTarget = svg.querySelector('.district-shape[tabindex="0"]');
        resetMapView();
        // Кнопка исчезает после reset: клавиатуре возвращаем точку входа в
        // карту, но tap/click не должен подсвечивать случайный район.
        if (event.detail === 0) {
            focusTarget?.focus({ preventScroll: true });
        } else if (svg.contains(document.activeElement)) {
            document.activeElement.blur();
        }
    });

    if ('ResizeObserver' in window) {
        resizeObserver = new ResizeObserver(() => {
            if ((!camera || !mapIsZoomed()) && svg.clientWidth > 0 && svg.clientHeight > 0) {
                resetMapView();
            }
        });
        resizeObserver.observe(svg);
    } else {
        window.addEventListener('resize', () => {
            if (!mapIsZoomed()) resetMapView();
        });
    }
}

function handleMapKeydown(event) {
    const path = event.currentTarget;
    const options = districtOptions();
    const currentIndex = options.findIndex(option => option.id === path.dataset.districtId);
    let nextIndex = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
        nextIndex = (currentIndex + 1) % options.length;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
        nextIndex = (currentIndex - 1 + options.length) % options.length;
    } else if (event.key === 'Home') {
        nextIndex = 0;
    } else if (event.key === 'End') {
        nextIndex = options.length - 1;
    } else if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault();
        setDraftDistrict(path.dataset.districtId);
        return;
    } else {
        return;
    }
    event.preventDefault();
    const next = document.querySelector(
        `#district-map [data-district-id="${CSS.escape(options[nextIndex].id)}"]`
    );
    if (next) {
        setDraftDistrict(options[nextIndex].id);
        next.focus();
    }
}

function renderGeometry(collection) {
    if (!collection || collection.type !== 'FeatureCollection' ||
            !Array.isArray(collection.features)) {
        throw new Error('Сервер вернул не GeoJSON FeatureCollection');
    }
    const options = districtOptions();
    const expectedIds = new Set(options.map(option => option.id));
    const featureIds = collection.features.map(feature => feature.properties && feature.properties.id);
    if (collection.features.length !== EXPECTED_DISTRICT_COUNT ||
            new Set(featureIds).size !== EXPECTED_DISTRICT_COUNT ||
            featureIds.some(id => !expectedIds.has(id))) {
        throw new Error('GeoJSON не содержит ожидаемые 18 районов');
    }

    const svg = document.getElementById('district-map');
    const { project } = projectedBounds(collection.features);
    const allPoints = [];
    svg.replaceChildren();

    options.forEach(option => {
        const feature = collection.features.find(item => item.properties.id === option.id);
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', featurePath(feature, project, point => allPoints.push(point)));
        path.setAttribute('class', 'district-shape');
        path.setAttribute('role', 'radio');
        path.setAttribute('tabindex', '-1');
        path.setAttribute('aria-label', option.name);
        path.setAttribute('aria-checked', 'false');
        path.dataset.districtId = option.id;
        path.addEventListener('click', () => {
            setDraftDistrict(option.id);
            path.focus({ preventScroll: true });
        });
        path.addEventListener('keydown', handleMapKeydown);
        svg.appendChild(path);
    });

    geometryBounds = pointsBounds(allPoints);
    if (!geometryBounds || geometryBounds.width <= 0 || geometryBounds.height <= 0) {
        throw new Error('Пустая или вырожденная SVG-геометрия');
    }
    svg.setAttribute('viewBox', FULL_VIEWBOX);
    camera = null;
    geometryRendered = true;
    updateCameraUi();
    updateSelectionUi();
}

async function ensureGeometry(openId) {
    const loading = document.getElementById('district-map-loading');
    const error = document.getElementById('district-map-error');
    const svg = document.getElementById('district-map');
    const reset = document.getElementById('district-map-reset');

    if (geometryRendered) {
        loading.classList.add('hidden');
        error.classList.add('hidden');
        svg.classList.remove('hidden');
        resetMapView();
        return;
    }

    loading.classList.remove('hidden');
    error.classList.add('hidden');
    svg.classList.add('hidden');
    reset.classList.add('hidden');
    if (!geometryPromise) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), GEOMETRY_TIMEOUT_MS);
        geometryPromise = fetch(GEOMETRY_URL, {
            headers: { Accept: 'application/geo+json, application/json' },
            signal: controller.signal,
        }).then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        }).then(collection => {
            renderGeometry(collection);
            return collection;
        }).catch(errorValue => {
            geometryPromise = null;
            throw errorValue;
        }).finally(() => {
            clearTimeout(timeout);
        });
    }

    try {
        await geometryPromise;
        if (openId !== pickerOpenId) return;
        loading.classList.add('hidden');
        svg.classList.remove('hidden');
        resetMapView();
    } catch (loadError) {
        console.error('Не удалось загрузить карту административных районов:', loadError);
        if (openId !== pickerOpenId) return;
        loading.classList.add('hidden');
        error.classList.remove('hidden');
        svg.classList.add('hidden');
        reset.classList.add('hidden');
    }
}

export function openDistrictPicker(currentDistrictId = null) {
    const openId = ++pickerOpenId;
    setDraftDistrict(currentDistrictId);
    showScreen('district-screen');
    window.scrollTo(0, 0);
    document.getElementById('district-screen-title').focus({ preventScroll: true });
    ensureGeometry(openId);
}

export function closeDistrictPicker({ restoreFocus = true } = {}) {
    pickerOpenId++;
    activePointers.clear();
    panGesture = null;
    pinchGesture = null;
    touchMapOwned = false;
    suppressMapClicksUntil = 0;
    document.getElementById('district-map')?.classList.remove('is-panning');
    showScreen('start-screen');
    if (restoreFocus) {
        // На низком мобильном viewport action может быть ниже fold. Обычный
        // focus возвращает пользователя и логически, и визуально к источнику.
        document.getElementById('district-picker-btn')?.focus();
    }
}

export function initDistrictPicker({ onConfirm }) {
    if (initialized) return true;

    const controls = {
        select: document.getElementById('district-list'),
        back: document.getElementById('district-back-btn'),
        cancel: document.getElementById('district-cancel-btn'),
        confirm: document.getElementById('district-confirm-btn'),
        map: document.getElementById('district-map'),
        reset: document.getElementById('district-map-reset'),
    };
    if (Object.values(controls).some(control => !control)) {
        console.warn('District picker markup is missing or incompatible');
        return false;
    }

    initialized = true;
    confirmSelection = onConfirm;
    initMapInteractions(controls.map, controls.reset);

    controls.select.addEventListener('change', event => {
        setDraftDistrict(event.target.value);
    });
    controls.back.addEventListener('click', () => {
        closeDistrictPicker();
    });
    controls.cancel.addEventListener('click', () => {
        closeDistrictPicker();
    });
    controls.confirm.addEventListener('click', () => {
        const name = districtName(draftDistrictId);
        if (!name) return;
        if (typeof confirmSelection === 'function') {
            confirmSelection({ id: draftDistrictId, name });
        }
        closeDistrictPicker();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' &&
                document.getElementById('district-screen').classList.contains('active')) {
            event.preventDefault();
            closeDistrictPicker();
        }
    });
    return true;
}

export function districtDisplayName(districtId) {
    return districtName(districtId);
}
