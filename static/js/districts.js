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
const CORE_DISTRICTS = new Set([
    'admiralteysky', 'vasileostrovsky', 'petrogradsky', 'tsentralny',
    'kirovsky', 'moskovsky', 'frunzensky', 'nevsky',
]);

let initialized = false;
let geometryPromise = null;
let geometryRendered = false;
let draftDistrictId = null;
let confirmSelection = null;
let pickerOpenId = 0;
let coreViewBox = null;
let coreZoomed = false;

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

function paddedViewBox(points) {
    if (!points.length) return null;
    let minX = Math.min(...points.map(point => point[0]));
    let maxX = Math.max(...points.map(point => point[0]));
    let minY = Math.min(...points.map(point => point[1]));
    let maxY = Math.max(...points.map(point => point[1]));
    const padX = Math.max(24, (maxX - minX) * 0.1);
    const padY = Math.max(24, (maxY - minY) * 0.1);
    minX = Math.max(0, minX - padX);
    maxX = Math.min(MAP_WIDTH, maxX + padX);
    minY = Math.max(0, minY - padY);
    maxY = Math.min(MAP_HEIGHT, maxY + padY);
    return `${minX.toFixed(1)} ${minY.toFixed(1)} ` +
        `${(maxX - minX).toFixed(1)} ${(maxY - minY).toFixed(1)}`;
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
    const corePoints = [];
    svg.replaceChildren();

    options.forEach(option => {
        const feature = collection.features.find(item => item.properties.id === option.id);
        const featurePoints = [];
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', featurePath(feature, project, point => featurePoints.push(point)));
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
        if (CORE_DISTRICTS.has(option.id)) corePoints.push(...featurePoints);
    });

    coreViewBox = paddedViewBox(corePoints);
    svg.setAttribute('viewBox', FULL_VIEWBOX);
    coreZoomed = false;
    geometryRendered = true;
    updateSelectionUi();
}

async function ensureGeometry(openId) {
    const loading = document.getElementById('district-map-loading');
    const error = document.getElementById('district-map-error');
    const svg = document.getElementById('district-map');
    const zoom = document.getElementById('district-map-zoom');

    if (geometryRendered) {
        loading.classList.add('hidden');
        error.classList.add('hidden');
        svg.classList.remove('hidden');
        zoom.classList.toggle('hidden', !coreViewBox);
        return;
    }

    loading.classList.remove('hidden');
    error.classList.add('hidden');
    svg.classList.add('hidden');
    zoom.classList.add('hidden');
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
        zoom.classList.toggle('hidden', !coreViewBox);
    } catch (loadError) {
        console.error('Не удалось загрузить карту административных районов:', loadError);
        if (openId !== pickerOpenId) return;
        loading.classList.add('hidden');
        error.classList.remove('hidden');
        svg.classList.add('hidden');
        zoom.classList.add('hidden');
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
        zoom: document.getElementById('district-map-zoom'),
    };
    if (Object.values(controls).some(control => !control)) {
        console.warn('District picker markup is missing or incompatible');
        return false;
    }

    initialized = true;
    confirmSelection = onConfirm;

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
    controls.zoom.addEventListener('click', event => {
        const svg = document.getElementById('district-map');
        coreZoomed = !coreZoomed;
        svg.setAttribute('viewBox', coreZoomed && coreViewBox ? coreViewBox : FULL_VIEWBOX);
        event.currentTarget.textContent = coreZoomed ? 'Показать весь город' : 'Центр крупнее';
        event.currentTarget.setAttribute('aria-pressed', String(coreZoomed));
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
