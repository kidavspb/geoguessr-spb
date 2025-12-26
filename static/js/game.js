/**
 * GeoGuessr СПб — Игровая логика
 */

// Глобальные переменные
let map = null;
let resultMap = null;
let panoramaPlayer = null;
let currentMarker = null;
let guessCoords = null;
let gameData = {
    totalRounds: 5,
    currentRound: 1,
    totalScore: 0,
    mode: 'panorama' // 'panorama' или 'photo'
};

// Координаты центра СПб
const SPB_CENTER = [59.9311, 30.3609];
const DEFAULT_ZOOM = 11;

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    initModeToggle();
    checkYandexMapsAPI();
});

/**
 * Инициализация переключателя режима
 */
function initModeToggle() {
    const modeBtns = document.querySelectorAll('.mode-btn');
    modeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.disabled) return;
            modeBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            gameData.mode = btn.dataset.mode;
            
            // Обновляем подсказку
            const hint = document.querySelector('.mode-hint');
            if (hint) {
                if (gameData.mode === 'panorama') {
                    hint.textContent = 'Панорамы — интерактивный обзор 360°';
                } else {
                    hint.textContent = 'Фото — статичные фотографии мест';
                }
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
                warning.style.cssText = 'color: #e94560; margin-top: 10px; font-size: 14px;';
                warning.textContent = '⚠️ Внимание: Проблема с загрузкой карт. Проверьте подключение к интернету.';
                startBtn.parentElement.insertBefore(warning, startBtn);
            }
        } else {
            console.log('Яндекс.Карты API загружен успешно');
        }
    }, 1000);
}

/**
 * Инициализация обработчиков событий
 */
function initEventListeners() {
    // Стартовый экран
    document.getElementById('start-btn').addEventListener('click', startGame);
    document.getElementById('show-leaderboard-btn').addEventListener('click', showLeaderboard);
    
    // Игровой экран
    document.getElementById('guess-btn').addEventListener('click', submitGuess);
    
    // Экран результата
    document.getElementById('next-round-btn').addEventListener('click', nextRound);
    
    // Финальный экран
    document.getElementById('play-again-btn').addEventListener('click', () => {
        showScreen('start-screen');
    });
    document.getElementById('final-leaderboard-btn').addEventListener('click', showLeaderboard);
    
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
        console.log('Начинаем игру для игрока:', playerName);
        const response = await fetch('/api/game/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ player_name: playerName })
        });
        
        console.log('Ответ сервера:', response.status, response.statusText);
        const data = await response.json();
        console.log('Данные ответа:', data);

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
    
    // Создаём элемент маркера
    const markerElement = document.createElement('div');
    markerElement.style.cssText = `
        width: 24px;
        height: 24px;
        background: #e94560;
        border: 3px solid white;
        border-radius: 50%;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        cursor: pointer;
        transform: translate(-50%, -50%);
    `;
    
    // Добавляем новый маркер
    currentMarker = new ymaps3.YMapMarker({
        coordinates: [lon, lat]
    }, markerElement);
    
    map.addChild(currentMarker);
    
    // Активируем кнопку
    document.getElementById('guess-btn').disabled = false;
}

/**
 * Загрузка текущей локации
 */
async function loadCurrentLocation() {
    const overlay = document.getElementById('photo-overlay');
    overlay.classList.remove('hidden');
    overlay.querySelector('span').textContent = 'Загрузка...';
    
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
            
            // Загружаем контент в зависимости от режима
            if (gameData.mode === 'panorama') {
                await loadPanorama(data.latitude, data.longitude);
            } else {
                loadPhoto(data.image_url);
            }
            
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
        
        console.log('Ищем панораму для координат:', latitude, longitude);
        
        // Ищем ближайшую панораму к заданной точке
        const panoramaResult = await ymaps.panorama.locate([latitude, longitude]);
        
        if (panoramaResult.length === 0) {
            throw new Error('Панорама не найдена в этой точке');
        }
        
        const panorama = panoramaResult[0];
        console.log('Найдена панорама:', panorama);
        
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
            overlay.querySelector('span').textContent = 'Панорама недоступна, пробуем другую точку...';
            console.error('Не удалось найти панораму:', fallbackError);
            // Запрашиваем новую точку
            setTimeout(() => loadCurrentLocation(), 1500);
        }
    }
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
        console.log('Сохранены реальные координаты панорамы:', latitude, longitude);
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
                console.log('Найдена ближайшая панорама со смещением:', latOffset, lonOffset);
                
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
    
    // Заполняем информацию
    document.getElementById('correct-location-name').textContent = data.correct_location.name;
    document.getElementById('correct-location-description').textContent = data.correct_location.description || '';
    
    // Форматирование расстояния
    const distanceText = data.distance_m < 1000 
        ? `${data.distance_m} м` 
        : `${data.distance_km} км`;
    document.getElementById('result-distance').textContent = distanceText;
    
    // Очки с цветом
    const scoreElement = document.getElementById('result-score');
    scoreElement.textContent = data.score;
    scoreElement.className = 'stat-value ' + getScoreClass(data.score);
    
    // Заголовок результата
    const title = document.getElementById('result-title');
    if (data.score >= 4500) {
        title.textContent = '🎯 Отлично!';
    } else if (data.score >= 3000) {
        title.textContent = '👍 Хорошо!';
    } else if (data.score >= 1000) {
        title.textContent = '😐 Неплохо';
    } else {
        title.textContent = '😅 Далековато...';
    }
    
    // Кнопка следующего раунда
    const nextBtn = document.getElementById('next-round-btn');
    if (data.is_game_over) {
        nextBtn.textContent = 'Посмотреть результаты';
        nextBtn.onclick = showFinalResults;
    } else {
        nextBtn.textContent = 'Следующий раунд';
        nextBtn.onclick = nextRound;
    }
    
    showScreen('result-screen');
    
    // Показываем карту с результатом
    await showResultMap(data);
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
    
    // Центр между двумя точками
    const centerLon = (correctLon + guessLon) / 2;
    const centerLat = (correctLat + guessLat) / 2;
    
    resultMap = new YMap(mapContainer, {
        location: {
            center: [centerLon, centerLat],
            zoom: 13
        }
    });
    
    resultMap.addChild(new YMapDefaultSchemeLayer());
    resultMap.addChild(new YMapDefaultFeaturesLayer());
    
    // Маркер правильной точки (зелёный)
    const correctMarkerEl = document.createElement('div');
    correctMarkerEl.style.cssText = `
        width: 28px;
        height: 28px;
        background: #28a745;
        border: 3px solid white;
        border-radius: 50%;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        transform: translate(-50%, -50%);
    `;
    const correctMarker = new YMapMarker({
        coordinates: [correctLon, correctLat]
    }, correctMarkerEl);
    resultMap.addChild(correctMarker);
    
    // Маркер угаданной точки (красный)
    const guessMarkerEl = document.createElement('div');
    guessMarkerEl.style.cssText = `
        width: 24px;
        height: 24px;
        background: #e94560;
        border: 3px solid white;
        border-radius: 50%;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        transform: translate(-50%, -50%);
    `;
    const guessMarker = new YMapMarker({
        coordinates: [guessLon, guessLat]
    }, guessMarkerEl);
    resultMap.addChild(guessMarker);
    
    // Линия между точками
    const lineFeature = new YMapFeature({
        geometry: {
            type: 'LineString',
            coordinates: [
                [guessLon, guessLat],
                [correctLon, correctLat]
            ]
        },
        style: {
            stroke: [{ color: '#e94560', width: 3, dash: [5, 5] }]
        }
    });
    resultMap.addChild(lineFeature);
}

/**
 * Переход к следующему раунду
 */
function nextRound() {
    showScreen('game-screen');
    loadCurrentLocation();
}

/**
 * Показ финальных результатов
 */
async function showFinalResults() {
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
                    
                    item.innerHTML = `
                        <span class="round-name">${round.round}. ${round.location_name}</span>
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
 * Показ таблицы лидеров
 */
async function showLeaderboard() {
    try {
        const response = await fetch('/api/leaderboard', {
            credentials: 'same-origin'
        });
        const data = await response.json();
        
        const tableContainer = document.getElementById('leaderboard-table');
        tableContainer.innerHTML = '';
        
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
                
                row.innerHTML = `
                    <span class="leaderboard-rank ${rankClass}">${entry.rank}</span>
                    <span class="leaderboard-name">${escapeHtml(entry.player_name)}</span>
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
