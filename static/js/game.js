/**
 * GeoGuessr СПб — Игровая логика
 */

// Глобальные переменные
let map = null;
let resultMap = null;
let panoramaPlayer = null;
let lastPanorama = null;      // панорама текущего раунда — для круга на экране результата
let resultPanoPlayer = null;  // мини-плеер в круге
let currentMarker = null;
let guessCoords = null;
let panoramaRetries = 0; // сколько раз перегенерировали точку из-за отсутствия панорамы
const MAX_PANORAMA_RETRIES = 8;
let gameData = {
    totalRounds: 5,
    currentRound: 1,
    totalScore: 0,
    difficulty: 'medium' // center, medium, hard
};

// Координаты центра СПб
const SPB_CENTER = [59.9311, 30.3609];
const DEFAULT_ZOOM = 11;

// Фирменные пины (из логотипа): красный — реальная точка, синий — догадка игрока
const PIN_RED = '/static/img/pin.svg';
const PIN_NAVY = '/static/img/pin-navy.svg';

/**
 * Склонение существительного по числу: plural(3, ['очко', 'очка', 'очков'])
 */
function plural(n, forms) {
    const abs = Math.abs(n) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return forms[2];
    if (last === 1) return forms[0];
    if (last >= 2 && last <= 4) return forms[1];
    return forms[2];
}

/**
 * Создать DOM-элемент фирменного пина для маркера на карте
 */
function createPinElement(src) {
    const img = document.createElement('img');
    img.src = src;
    img.className = 'map-pin';
    img.alt = '';
    return img;
}

// Описания сложности
const DIFFICULTY_HINTS = {
    'center': 'Центр — исторический центр города',
    'medium': 'Средняя — центр и ближайшие районы',
    'hard': 'Весь город — от центра до окраин'
};

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    initDifficultyToggle();
    checkYandexMapsAPI();
});

/**
 * Инициализация переключателя сложности
 */
function initDifficultyToggle() {
    const difficultyBtns = document.querySelectorAll('.difficulty-btn');
    difficultyBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            difficultyBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            gameData.difficulty = btn.dataset.difficulty;
            
            // Обновляем подсказку
            const hint = document.querySelector('.difficulty-hint');
            if (hint) {
                hint.textContent = DIFFICULTY_HINTS[gameData.difficulty];
            }
        });
    });
}

/**
 * Проверка загрузки Яндекс.Карт API
 */
function checkYandexMapsAPI() {
    // Даём время на загрузку скрипта
    setTimeout(() => {
        if (typeof ymaps3 === 'undefined') {
            console.error('Яндекс.Карты API не загружен');
            // Показываем предупреждение пользователю
            const startBtn = document.getElementById('start-btn');
            if (startBtn) {
                const warning = document.createElement('div');
                warning.className = 'api-warning';
                warning.textContent = '⚠️ Проблема с загрузкой карт. Проверьте подключение к интернету.';
                startBtn.parentElement.insertBefore(warning, startBtn);
            }
        }
    }, 1000);
}

/**
 * Инициализация обработчиков событий
 */
function initEventListeners() {
    // Стартовый экран
    document.getElementById('start-btn').addEventListener('click', startGame);
    document.getElementById('show-leaderboard-btn').addEventListener('click', openLeaderboard);

    // Игровой экран
    document.getElementById('guess-btn').addEventListener('click', submitGuess);

    // Экран результата
    document.getElementById('next-round-btn').addEventListener('click', nextRound);

    // Финальный экран
    document.getElementById('play-again-btn').addEventListener('click', () => {
        showScreen('start-screen');
    });
    document.getElementById('final-leaderboard-btn').addEventListener('click', openLeaderboard);

    // Фильтр таблицы лидеров по сложности
    document.querySelectorAll('.lb-filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.lb-filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            showLeaderboard(btn.dataset.difficulty);
        });
    });
    
    // Таблица лидеров
    document.getElementById('back-btn').addEventListener('click', () => {
        showScreen('start-screen');
    });
    
    // Enter для начала игры
    document.getElementById('player-name').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') startGame();
    });
}

/**
 * Переключение экранов
 */
function showScreen(screenId) {
    document.querySelectorAll('.screen').forEach(screen => {
        screen.classList.remove('active');
    });
    document.getElementById(screenId).classList.add('active');
}

/**
 * Начало новой игры
 */
async function startGame() {
    const playerName = document.getElementById('player-name').value.trim() || 'Аноним';
    
    try {
        const response = await fetch('/api/game/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ 
                player_name: playerName,
                difficulty: gameData.difficulty
            })
        });
        
        const data = await response.json();

        if (response.ok) {
            gameData.totalRounds = data.total_rounds;
            gameData.currentRound = 1;
            gameData.totalScore = 0;
            
            document.getElementById('total-rounds').textContent = gameData.totalRounds;
            document.getElementById('total-score').textContent = '0';
            
            showScreen('game-screen');

            try {
                await initMap();
                loadCurrentLocation();
            } catch (mapError) {
                console.error('Ошибка инициализации карты:', mapError);
                alert('Не удалось загрузить карту. Проверьте подключение к интернету и перезагрузите страницу.');
                showScreen('start-screen');
            }
        } else {
            alert('Ошибка: ' + data.error);
        }
    } catch (error) {
        console.error('Ошибка запуска игры:', error);
        console.error('Тип ошибки:', error.name);
        console.error('Сообщение ошибки:', error.message);
        alert('Не удалось начать игру. Проверьте подключение. Детали: ' + error.message);
    }
}

/**
 * Инициализация карты Яндекс
 */
async function initMap() {
    try {
        // Проверяем, что API загружен
        if (typeof ymaps3 === 'undefined') {
            throw new Error('Яндекс.Карты API не загружен. Проверьте подключение к интернету.');
        }

        // Ждём загрузки API
        await ymaps3.ready;

        const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapMarker, YMapListener } = ymaps3;

        // Очищаем контейнер
        const mapContainer = document.getElementById('map');
        mapContainer.innerHTML = '';

        // Создаём карту
        map = new YMap(mapContainer, {
            location: {
                center: [SPB_CENTER[1], SPB_CENTER[0]], // [lon, lat]
                zoom: DEFAULT_ZOOM
            }
        });

        // Добавляем слои
        map.addChild(new YMapDefaultSchemeLayer());
        map.addChild(new YMapDefaultFeaturesLayer());

        // Обработчик клика по карте
        const clickListener = new YMapListener({
            layer: 'any',
            onClick: (object, event) => {
                if (event && event.coordinates) {
                    placeMarker(event.coordinates);
                }
            }
        });
        map.addChild(clickListener);

        // Сброс маркера
        currentMarker = null;
        guessCoords = null;
        document.getElementById('guess-btn').disabled = true;
    } catch (error) {
        console.error('Ошибка инициализации карты:', error);
        throw error;
    }
}

/**
 * Размещение маркера на карте
 */
function placeMarker(coordinates) {
    const [lon, lat] = coordinates;
    guessCoords = { latitude: lat, longitude: lon };

    // Удаляем старый маркер
    if (currentMarker) {
        map.removeChild(currentMarker);
    }

    // Добавляем новый маркер — фирменный синий пин
    currentMarker = new ymaps3.YMapMarker({
        coordinates: [lon, lat]
    }, createPinElement(PIN_NAVY));

    map.addChild(currentMarker);

    // Прячем подсказку и активируем кнопку
    const hint = document.getElementById('map-hint');
    if (hint) hint.classList.add('hidden');
    document.getElementById('guess-btn').disabled = false;
}

/**
 * Загрузка текущей локации
 */
async function loadCurrentLocation() {
    const overlay = document.getElementById('photo-overlay');
    overlay.classList.remove('hidden');
    overlay.querySelector('span').textContent = 'Загрузка...';
    
    panoramaRetries = 0; // новый раунд — сбрасываем счётчик попыток поиска панорамы

    try {
        const response = await fetch('/api/game/location', {
            credentials: 'same-origin'
        });
        const data = await response.json();

        if (data.game_over) {
            showFinalResults();
            return;
        }

        if (response.ok) {
            // Обновляем информацию о раунде
            gameData.currentRound = data.round;
            document.getElementById('current-round').textContent = data.round;
            
            // Загружаем панораму
            await loadPanorama(data.latitude, data.longitude);
            
            // Сбрасываем карту
            resetMapForNewRound();
        } else {
            alert('Ошибка: ' + data.error);
        }
    } catch (error) {
        console.error('Ошибка загрузки локации:', error);
        overlay.querySelector('span').textContent = 'Ошибка загрузки';
    }
}

/**
 * Загрузка фото (режим photo)
 */
function loadPhoto(imageUrl) {
    const overlay = document.getElementById('photo-overlay');
    
    // Показываем режим фото, скрываем панораму
    document.getElementById('photo-mode').classList.remove('hidden');
    document.getElementById('panorama-mode').classList.add('hidden');
    
    const img = document.getElementById('location-photo');
    img.onload = () => {
        overlay.classList.add('hidden');
    };
    img.onerror = () => {
        overlay.querySelector('span').textContent = 'Ошибка загрузки фото';
    };
    img.src = imageUrl;
}

/**
 * Загрузка панорамы Яндекс (режим panorama) через API v2
 */
async function loadPanorama(latitude, longitude) {
    const overlay = document.getElementById('photo-overlay');
    
    // Показываем режим панорамы, скрываем фото
    document.getElementById('photo-mode').classList.add('hidden');
    document.getElementById('panorama-mode').classList.remove('hidden');
    
    const panoramaContainer = document.getElementById('panorama-player');
    panoramaContainer.innerHTML = '';
    
    try {
        // Ждём загрузки ymaps (API v2)
        await new Promise((resolve, reject) => {
            if (typeof ymaps !== 'undefined' && ymaps.ready) {
                ymaps.ready(resolve);
            } else {
                // Ждём загрузки API
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
            }
        });
        
        
        // Ищем ближайшую панораму к заданной точке
        const panoramaResult = await ymaps.panorama.locate([latitude, longitude]);
        
        if (panoramaResult.length === 0) {
            throw new Error('Панорама не найдена в этой точке');
        }
        
        const panorama = panoramaResult[0];
        lastPanorama = panorama;

        // Получаем реальные координаты панорамы и сохраняем на сервер
        const position = panorama.getPosition();
        await saveActualPoint(position[0], position[1]);
        
        // Создаём плеер панорамы
        panoramaPlayer = new ymaps.panorama.Player(panoramaContainer, panorama, {
            controls: ['zoomControl', 'fullscreenControl'],
            direction: [0, 0], // Начальное направление взгляда (азимут, наклон)
            span: [130, 80],   // Угол обзора
            suppressMapOpenBlock: true // Скрыть кнопку "Открыть в Яндекс.Картах"
        });
        
        // Ждём загрузки панорамы
        panoramaPlayer.events.add('panoramachange', () => {
            overlay.classList.add('hidden');
        });
        
        // Также скрываем оверлей через таймаут на случай если событие не сработает
        setTimeout(() => {
            overlay.classList.add('hidden');
        }, 2000);
        
    } catch (error) {
        console.error('Ошибка загрузки панорамы:', error);
        
        // Пробуем найти ближайшую панораму в радиусе
        try {
            await findNearestPanorama(latitude, longitude, overlay, panoramaContainer);
        } catch (fallbackError) {
            console.error('Не удалось найти панораму:', fallbackError);

            // Защита от бесконечного цикла: ограничиваем число перегенераций.
            if (panoramaRetries >= MAX_PANORAMA_RETRIES) {
                overlay.querySelector('span').textContent =
                    'Не удалось найти панораму поблизости. Начните игру заново.';
                return;
            }

            panoramaRetries++;
            overlay.querySelector('span').textContent =
                'Здесь нет панорамы, выбираем другое место...';

            // Просим сервер сгенерировать НОВУЮ точку для этого раунда
            // (раньше повторялась та же точка — отсюда бесконечный цикл).
            const newPoint = await skipLocation();
            if (newPoint) {
                await loadPanorama(newPoint.latitude, newPoint.longitude);
            } else {
                overlay.querySelector('span').textContent = 'Ошибка загрузки. Попробуйте перезагрузить страницу.';
            }
        }
    }
}

/**
 * Запросить у сервера новую точку для текущего раунда
 * (когда в текущей точке нет панорамы).
 */
async function skipLocation() {
    try {
        const response = await fetch('/api/game/skip_location', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin'
        });
        if (response.ok) {
            return await response.json();
        }
    } catch (error) {
        console.error('Ошибка перегенерации точки:', error);
    }
    return null;
}

/**
 * Сохранение реальных координат панорамы на сервере
 */
async function saveActualPoint(latitude, longitude) {
    try {
        await fetch('/api/game/set_actual_point', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ latitude, longitude })
        });
    } catch (error) {
        console.error('Ошибка сохранения координат:', error);
    }
}

/**
 * Поиск ближайшей панорамы в радиусе
 */
async function findNearestPanorama(latitude, longitude, overlay, container) {
    overlay.querySelector('span').textContent = 'Поиск ближайшей панорамы...';
    
    // Пробуем несколько точек вокруг оригинальной
    const offsets = [
        [0.0005, 0], [-0.0005, 0], [0, 0.0005], [0, -0.0005],
        [0.001, 0], [-0.001, 0], [0, 0.001], [0, -0.001],
        [0.002, 0], [-0.002, 0], [0, 0.002], [0, -0.002],
        [0.0015, 0.0015], [-0.0015, 0.0015], [0.0015, -0.0015], [-0.0015, -0.0015]
    ];
    
    for (const [latOffset, lonOffset] of offsets) {
        try {
            const result = await ymaps.panorama.locate([latitude + latOffset, longitude + lonOffset]);
            if (result.length > 0) {
                const panorama = result[0];
                lastPanorama = panorama;

                // Сохраняем реальные координаты найденной панорамы
                const position = panorama.getPosition();
                await saveActualPoint(position[0], position[1]);
                
                panoramaPlayer = new ymaps.panorama.Player(container, panorama, {
                    controls: ['zoomControl', 'fullscreenControl'],
                    direction: [0, 0],
                    span: [130, 80],
                    suppressMapOpenBlock: true
                });
                
                setTimeout(() => {
                    overlay.classList.add('hidden');
                }, 1500);
                
                return;
            }
        } catch (e) {
            // Продолжаем поиск
        }
    }
    
    throw new Error('Панорама не найдена в радиусе');
}

/**
 * Сброс карты для нового раунда
 */
function resetMapForNewRound() {
    if (currentMarker && map) {
        map.removeChild(currentMarker);
        currentMarker = null;
    }
    guessCoords = null;
    document.getElementById('guess-btn').disabled = true;

    // Возвращаем подсказку
    const hint = document.getElementById('map-hint');
    if (hint) hint.classList.remove('hidden');

    // Центрируем карту
    if (map) {
        map.setLocation({
            center: [SPB_CENTER[1], SPB_CENTER[0]],
            zoom: DEFAULT_ZOOM,
            duration: 500
        });
    }
}

/**
 * Отправка ответа
 */
async function submitGuess() {
    if (!guessCoords) {
        alert('Отметьте точку на карте!');
        return;
    }
    
    document.getElementById('guess-btn').disabled = true;
    
    try {
        const response = await fetch('/api/game/guess', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(guessCoords)
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showRoundResult(data);
        } else {
            alert('Ошибка: ' + data.error);
        }
    } catch (error) {
        console.error('Ошибка отправки ответа:', error);
        alert('Не удалось отправить ответ.');
    }
}

/**
 * Показ результата раунда
 */
async function showRoundResult(data) {
    gameData.totalScore = data.total_score;
    document.getElementById('total-score').textContent = data.total_score;

    // Название места показываем только если оно осмысленное,
    // а не сгенерированное («Точка раунда N»)
    const name = (data.correct_location.name || '').trim();
    const locationBlock = document.getElementById('result-location');
    if (name && !/^Точка раунда/i.test(name)) {
        document.getElementById('correct-location-name').textContent = name;
        locationBlock.classList.remove('hidden');
    } else {
        locationBlock.classList.add('hidden');
    }

    // Карточку «Знал ли ты, что …» показываем только с настоящим фактом,
    // а не с заглушкой «Случайное место …»
    const description = (data.correct_location.description || '').trim();
    const factCard = document.getElementById('fact-card');
    if (description && !/^Случайное место/i.test(description)) {
        document.getElementById('correct-location-description').textContent = description;
        factCard.classList.remove('hidden');
    } else {
        factCard.classList.add('hidden');
    }

    // Форматирование расстояния
    const distanceText = data.distance_m < 1000
        ? `${data.distance_m} ${plural(data.distance_m, ['метр', 'метра', 'метров'])}`
        : `${data.distance_km} км`;
    document.getElementById('result-distance').textContent = distanceText;

    // Очки за раунд
    document.getElementById('result-score').textContent = data.score;
    document.getElementById('result-score-word').textContent =
        plural(data.score, ['очко', 'очка', 'очков']);

    // Заголовок результата
    const title = document.getElementById('result-title');
    if (data.score >= 4500) {
        title.textContent = 'В яблочко!';
    } else if (data.score >= 3000) {
        title.textContent = 'Отлично!';
    } else if (data.score >= 1000) {
        title.textContent = 'Неплохо';
    } else {
        title.textContent = 'Далековато…';
    }

    // Кнопка следующего раунда
    const nextBtn = document.getElementById('next-round-btn');
    if (data.is_game_over) {
        nextBtn.textContent = 'Посмотреть результаты';
        nextBtn.onclick = showFinalResults;
    } else {
        nextBtn.textContent = 'Продолжить';
        nextBtn.onclick = nextRound;
    }

    showScreen('result-screen');

    // Панорама раунда в круге + карта с результатом
    showResultPano();
    await showResultMap(data);
}

/**
 * Мини-плеер с панорамой раунда в круглом окне панели результата:
 * к месту можно вернуться и осмотреться ещё раз
 */
function showResultPano() {
    const container = document.getElementById('result-pano');
    destroyResultPano();
    container.innerHTML = '';

    if (!lastPanorama || typeof ymaps === 'undefined') {
        container.classList.add('hidden');
        return;
    }

    try {
        resultPanoPlayer = new ymaps.panorama.Player(container, lastPanorama, {
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
 * Уничтожить мини-плеер при уходе с экрана результата
 */
function destroyResultPano() {
    if (resultPanoPlayer) {
        try {
            resultPanoPlayer.destroy();
        } catch (error) {
            // плеер мог уже умереть вместе с DOM-узлом
        }
        resultPanoPlayer = null;
    }
}

/**
 * Показ карты результата с линией между точками
 */
async function showResultMap(data) {
    await ymaps3.ready;

    const { YMap, YMapDefaultSchemeLayer, YMapDefaultFeaturesLayer, YMapMarker, YMapFeature } = ymaps3;

    const mapContainer = document.getElementById('result-map');
    mapContainer.innerHTML = '';

    const correctLon = data.correct_location.longitude;
    const correctLat = data.correct_location.latitude;
    const guessLon = data.guess.longitude;
    const guessLat = data.guess.latitude;

    // Сами вписываем обе точки в кадр: встроенное вписывание bounds в v3
    // срабатывает до того, как карта узнаёт размер контейнера.
    // Панель результата закрывает часть карты (справа на десктопе, снизу
    // на мобильной раскладке) — учитываем это и в масштабе, и в центре.
    const isMobile = window.innerWidth <= 720;
    const stageW = window.innerWidth;
    const stageH = window.innerHeight;
    const panelRight = isMobile ? 0 : Math.min(380, stageW);
    const panelBottom = isMobile ? stageH * 0.62 : 0;
    const padPx = 70;

    const TILE = 256;
    const clampLat = (lat) => Math.max(-85, Math.min(85, lat));
    // Нормированная меркаторская Y-координата (0..1)
    const mercY = (lat) => {
        const rad = clampLat(lat) * Math.PI / 180;
        return (1 - Math.log(Math.tan(Math.PI / 4 + rad / 2)) / Math.PI) / 2;
    };
    const mercYInv = (y) => (2 * Math.atan(Math.exp(Math.PI * (1 - 2 * y))) - Math.PI / 2) * 180 / Math.PI;

    // Минимальные охваты ~50 м: при точном попадании не упираемся в ноль,
    // а при маленьком промахе карта приближается к кварталу, не к городу
    const spanLon = Math.max(Math.abs(correctLon - guessLon), 0.0009) / 360;
    const spanY = Math.max(Math.abs(mercY(correctLat) - mercY(guessLat)), 0.0000012);
    const availW = Math.max(stageW - panelRight - padPx * 2, 200);
    const availH = Math.max(stageH - panelBottom - padPx * 2, 200);
    // Округляем вниз: дробный zoom карта округлит вверх, и точки выйдут за кадр
    const zoom = Math.floor(Math.max(3, Math.min(18,
        Math.min(
            Math.log2(availW / TILE / spanLon),
            Math.log2(availH / TILE / spanY)
        )
    )));

    // Геометрический центр двух точек, сдвинутый так, чтобы он оказался
    // в середине свободной от панели области
    const worldPx = TILE * Math.pow(2, zoom);
    const centerLon = (correctLon + guessLon) / 2 + (panelRight / 2) * (360 / worldPx);
    const centerY = (mercY(correctLat) + mercY(guessLat)) / 2 + (panelBottom / 2) / worldPx;
    const centerLat = mercYInv(centerY);

    resultMap = new YMap(mapContainer, {
        location: {
            center: [centerLon, centerLat],
            zoom
        }
    });

    resultMap.addChild(new YMapDefaultSchemeLayer());
    resultMap.addChild(new YMapDefaultFeaturesLayer());

    // Пунктирная линия между догадкой и реальной точкой
    const lineFeature = new YMapFeature({
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
    });
    resultMap.addChild(lineFeature);

    // Реальная точка — фирменный красный пин
    const correctMarker = new YMapMarker({
        coordinates: [correctLon, correctLat]
    }, createPinElement(PIN_RED));
    resultMap.addChild(correctMarker);

    // Догадка игрока — синий пин
    const guessMarker = new YMapMarker({
        coordinates: [guessLon, guessLat]
    }, createPinElement(PIN_NAVY));
    resultMap.addChild(guessMarker);
}

/**
 * Переход к следующему раунду
 */
function nextRound() {
    destroyResultPano();
    showScreen('game-screen');
    loadCurrentLocation();
}

/**
 * Показ финальных результатов
 */
async function showFinalResults() {
    destroyResultPano();
    try {
        const response = await fetch('/api/game/results', {
            credentials: 'same-origin'
        });
        const data = await response.json();
        
        if (response.ok) {
            document.getElementById('final-score').textContent = data.total_score;
            
            // Формируем список раундов
            const summaryContainer = document.getElementById('rounds-summary');
            summaryContainer.innerHTML = '';
            
            if (data.rounds && data.rounds.length > 0) {
                data.rounds.forEach(round => {
                    const item = document.createElement('div');
                    item.className = 'round-item';
                    
                    const distanceText = round.distance_m < 1000
                        ? `${round.distance_m} м`
                        : `${(round.distance_m / 1000).toFixed(1)} км`;

                    // Без дублирования, когда имя локации — заглушка «Раунд N»
                    const roundLabel = /^Раунд \d+$/i.test(round.location_name || '')
                        ? `Раунд ${round.round}`
                        : `${round.round}. ${round.location_name}`;

                    item.innerHTML = `
                        <span class="round-name">${roundLabel}</span>
                        <span class="round-distance">${distanceText}</span>
                        <span class="round-score ${getScoreClass(round.score)}">${round.score}</span>
                    `;
                    summaryContainer.appendChild(item);
                });
            }
            
            showScreen('final-screen');
        }
    } catch (error) {
        console.error('Ошибка получения результатов:', error);
        showScreen('final-screen');
    }
}

/**
 * Открыть таблицу лидеров заново: сбрасываем фильтр на «Все».
 */
function openLeaderboard() {
    document.querySelectorAll('.lb-filter-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.difficulty === 'all');
    });
    showLeaderboard('all');
}

/**
 * Показ таблицы лидеров. difficulty: 'all' | 'center' | 'medium' | 'hard'.
 * Бейдж сложности показываем только в общем списке — в отфильтрованном он избыточен.
 */
async function showLeaderboard(difficulty = 'all') {
    const url = difficulty && difficulty !== 'all'
        ? `/api/leaderboard?difficulty=${encodeURIComponent(difficulty)}`
        : '/api/leaderboard';

    try {
        const response = await fetch(url, {
            credentials: 'same-origin'
        });
        const data = await response.json();

        const tableContainer = document.getElementById('leaderboard-table');
        tableContainer.innerHTML = '';

        const showBadge = (difficulty === 'all');

        if (data.leaderboard && data.leaderboard.length > 0) {
            // Заголовок
            const header = document.createElement('div');
            header.className = 'leaderboard-row header';
            header.innerHTML = `
                <span class="leaderboard-rank">#</span>
                <span class="leaderboard-name">Игрок</span>
                <span class="leaderboard-score">Очки</span>
                <span class="leaderboard-date">Дата</span>
            `;
            tableContainer.appendChild(header);

            // Строки
            data.leaderboard.forEach(entry => {
                const row = document.createElement('div');
                row.className = 'leaderboard-row';

                let rankClass = '';
                if (entry.rank === 1) rankClass = 'gold';
                else if (entry.rank === 2) rankClass = 'silver';
                else if (entry.rank === 3) rankClass = 'bronze';

                const badge = showBadge && entry.difficulty_name
                    ? `<span class="lb-badge lb-badge-${escapeHtml(entry.difficulty || '')}">${escapeHtml(entry.difficulty_name)}</span>`
                    : '';

                row.innerHTML = `
                    <span class="leaderboard-rank ${rankClass}">${entry.rank}</span>
                    <span class="leaderboard-name">${escapeHtml(entry.player_name)}${badge}</span>
                    <span class="leaderboard-score">${entry.total_score}</span>
                    <span class="leaderboard-date">${entry.date || ''}</span>
                `;
                tableContainer.appendChild(row);
            });
        } else {
            tableContainer.innerHTML = '<div class="leaderboard-empty">Пока нет результатов</div>';
        }

        showScreen('leaderboard-screen');
    } catch (error) {
        console.error('Ошибка загрузки таблицы лидеров:', error);
    }
}

/**
 * Получение CSS класса для очков
 */
function getScoreClass(score) {
    if (score >= 4000) return 'score-excellent';
    if (score >= 2500) return 'score-good';
    if (score >= 1000) return 'score-average';
    return 'score-poor';
}

/**
 * Экранирование HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
