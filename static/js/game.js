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
let finalMap = null;          // карта всех раундов на финальном экране
let roundTimerInterval = null; // интервал обратного отсчёта раунда
let roundDeadline = null;      // момент окончания времени раунда (ms)
let guessSubmitting = false;   // защита от двойной отправки (клик + таймер)
let gameData = {
    totalRounds: 5,
    currentRound: 1,
    totalScore: 0,
    difficulty: 'medium', // center, medium, hard
    timeLimit: 0,         // секунд на раунд, 0 — без лимита
    challengeToken: null  // токен челленджа из ссылки-вызова
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

/**
 * Меркаторская математика для ручного вписывания точек в кадр:
 * встроенное вписывание bounds в API v3 срабатывает до того,
 * как карта узнаёт размер контейнера.
 */
const TILE_SIZE = 256;

function mercatorY(lat) {
    const clamped = Math.max(-85, Math.min(85, lat));
    const rad = clamped * Math.PI / 180;
    return (1 - Math.log(Math.tan(Math.PI / 4 + rad / 2)) / Math.PI) / 2;
}

function mercatorYInv(y) {
    return (2 * Math.atan(Math.exp(Math.PI * (1 - 2 * y))) - Math.PI / 2) * 180 / Math.PI;
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
    initTimerToggle();
    initChallengeFromUrl();
    checkYandexMapsAPI();
});

/**
 * Инициализация переключателя лимита времени на раунд
 */
function initTimerToggle() {
    const timerBtns = document.querySelectorAll('.timer-btn');
    timerBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            timerBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            gameData.timeLimit = parseInt(btn.dataset.timer, 10) || 0;
        });
    });
}

/**
 * Заход по ссылке-вызову (?challenge=TOKEN): показываем баннер с данными
 * автора челленджа; сложность и таймер берутся из его игры.
 */
async function initChallengeFromUrl() {
    const token = new URLSearchParams(window.location.search).get('challenge');
    if (!token) return;

    const banner = document.getElementById('challenge-banner');
    try {
        const response = await fetch(`/api/challenge/${encodeURIComponent(token)}`, {
            credentials: 'same-origin'
        });
        const data = await response.json();

        if (!response.ok) {
            banner.textContent = 'Челлендж не найден — можно сыграть обычную игру.';
            banner.classList.remove('hidden');
            return;
        }

        gameData.challengeToken = token;
        const timerText = data.time_limit
            ? `, ${Math.round(data.time_limit / 60) || 1} мин на раунд`
            : '';
        banner.innerHTML = `⚔️ <strong>${escapeHtml(data.player_name)}</strong> бросает тебе вызов!<br>` +
            `Счёт: <strong>${data.total_score}</strong> ` +
            `(${escapeHtml(data.difficulty_name || '')}${timerText}). Те же точки — сможешь лучше?`;
        banner.classList.remove('hidden');

        // Параметры фиксированы челленджем — селекторы прячем
        document.getElementById('difficulty-group').classList.add('hidden');
        document.getElementById('timer-group').classList.add('hidden');
    } catch (error) {
        console.error('Ошибка загрузки челленджа:', error);
    }
}

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
    document.getElementById('challenge-btn').addEventListener('click', copyChallengeLink);

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
                difficulty: gameData.difficulty,
                time_limit: gameData.timeLimit || null,
                challenge_token: gameData.challengeToken
            })
        });

        const data = await response.json();

        if (response.ok) {
            gameData.totalRounds = data.total_rounds;
            gameData.currentRound = 1;
            gameData.totalScore = 0;
            gameData.timeLimit = data.time_limit || 0; // серверное значение — истина

            // Челлендж одноразовый: следующая игра с этими же точками не имеет
            // смысла — игрок их уже видел. Возвращаем обычный стартовый экран.
            if (gameData.challengeToken) {
                gameData.challengeToken = null;
                document.getElementById('challenge-banner').classList.add('hidden');
                document.getElementById('difficulty-group').classList.remove('hidden');
                document.getElementById('timer-group').classList.remove('hidden');
                window.history.replaceState(null, '', window.location.pathname);
            }

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

            // Панорама на экране — запускаем отсчёт времени раунда
            startRoundTimer();
        } else {
            alert('Ошибка: ' + data.error);
        }
    } catch (error) {
        console.error('Ошибка загрузки локации:', error);
        overlay.querySelector('span').textContent = 'Ошибка загрузки';
    }
}

/**
 * Дождаться загрузки JS API v2 (панорамы). Скрипт подключён в <head>,
 * но мог ещё не доехать по сети.
 */
function ymapsV2Ready() {
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
async function locatePanorama(latitude, longitude) {
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
 * Загрузка панорамы Яндекс через API v2.
 * Если панорамы нет и рядом — просит у сервера новую точку раунда (до лимита).
 */
async function loadPanorama(latitude, longitude) {
    const overlay = document.getElementById('photo-overlay');

    // Уничтожаем плеер прошлого раунда ДО очистки контейнера: иначе он
    // продолжает жить, опрашивает вырванный из DOM элемент и заваливает
    // консоль ошибками offsetWidth (заметно тормозит страницу).
    destroyPanoramaPlayer();
    const panoramaContainer = document.getElementById('panorama-player');
    panoramaContainer.innerHTML = '';

    try {
        await ymapsV2Ready();
    } catch (error) {
        console.error('Ошибка загрузки панорамы:', error);
        overlay.querySelector('span').textContent = 'Ошибка загрузки. Попробуйте перезагрузить страницу.';
        return;
    }

    const panorama = await locatePanorama(latitude, longitude);

    if (!panorama) {
        // Панорамы нет — защищаемся от бесконечного цикла перегенераций
        if (panoramaRetries >= MAX_PANORAMA_RETRIES) {
            overlay.querySelector('span').textContent =
                'Не удалось найти панораму поблизости. Начните игру заново.';
            return;
        }
        panoramaRetries++;
        overlay.querySelector('span').textContent = 'Здесь нет панорамы, выбираем другое место...';

        const newPoint = await skipLocation();
        if (newPoint) {
            await loadPanorama(newPoint.latitude, newPoint.longitude);
        } else {
            overlay.querySelector('span').textContent = 'Ошибка загрузки. Попробуйте перезагрузить страницу.';
        }
        return;
    }

    lastPanorama = panorama;

    // Реальные координаты панорамы — на сервер (по ним считаются очки)
    const position = panorama.getPosition();
    await saveActualPoint(position[0], position[1]);

    panoramaPlayer = new ymaps.panorama.Player(panoramaContainer, panorama, {
        controls: ['zoomControl', 'fullscreenControl'],
        direction: [0, 0], // Начальное направление взгляда (азимут, наклон)
        span: [130, 80],   // Угол обзора
        suppressMapOpenBlock: true // Скрыть кнопку «Открыть в Яндекс.Картах»
    });

    // Прячем оверлей по факту загрузки (и по таймауту — на случай, если
    // событие не сработает)
    panoramaPlayer.events.add('panoramachange', () => overlay.classList.add('hidden'));
    setTimeout(() => overlay.classList.add('hidden'), 2000);
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
 * Запустить обратный отсчёт раунда (если игра с лимитом времени)
 */
function startRoundTimer() {
    stopRoundTimer();
    const chip = document.getElementById('timer-chip');
    if (!gameData.timeLimit) {
        chip.classList.add('hidden');
        return;
    }

    roundDeadline = Date.now() + gameData.timeLimit * 1000;
    chip.classList.remove('hidden', 'timer-low');
    updateTimerDisplay();
    roundTimerInterval = setInterval(updateTimerDisplay, 250);
}

function updateTimerDisplay() {
    const chip = document.getElementById('timer-chip');
    const secondsLeft = Math.max(0, Math.ceil((roundDeadline - Date.now()) / 1000));

    const min = Math.floor(secondsLeft / 60);
    const sec = String(secondsLeft % 60).padStart(2, '0');
    document.getElementById('timer-value').textContent = `${min}:${sec}`;

    if (secondsLeft <= 10) chip.classList.add('timer-low');

    if (secondsLeft <= 0) {
        stopRoundTimer();
        onRoundTimeExpired();
    }
}

function stopRoundTimer() {
    if (roundTimerInterval) {
        clearInterval(roundTimerInterval);
        roundTimerInterval = null;
    }
}

/**
 * Время раунда вышло: отправляем ответ с тем, что есть.
 * Если точка отмечена — обычная догадка, нет — раунд завершается с 0 очков.
 */
function onRoundTimeExpired() {
    if (guessCoords) {
        submitGuess();
    } else {
        sendGuess({ timed_out: true });
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
    sendGuess(guessCoords);
}

/**
 * Отправить тело догадки на сервер и показать результат раунда.
 * Единая точка отправки для клика по кнопке и истечения таймера.
 */
async function sendGuess(payload) {
    if (guessSubmitting) return;
    guessSubmitting = true;
    stopRoundTimer();

    try {
        const response = await fetch('/api/game/guess', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(payload)
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
    } finally {
        guessSubmitting = false;
    }
}

/**
 * Показ результата раунда
 */
async function showRoundResult(data) {
    gameData.totalScore = data.total_score;
    document.getElementById('total-score').textContent = data.total_score;

    // Адрес точки («Это было: …»): серверный, а если сервер без ключа
    // геокодера — клиентский, через JS API v2 с обычным ключом карт
    const locationBlock = document.getElementById('result-location');
    if (data.address) {
        document.getElementById('correct-location-name').textContent = data.address;
        locationBlock.classList.remove('hidden');
    } else {
        locationBlock.classList.add('hidden');
        clientReverseGeocode(
            data.correct_location.latitude,
            data.correct_location.longitude
        ).then(address => {
            if (address) {
                document.getElementById('correct-location-name').textContent = address;
                locationBlock.classList.remove('hidden');
            }
        });
    }

    // Строка промаха: обычный текст или сообщение о таймауте
    const missBlock = document.getElementById('result-miss');
    if (data.guess && data.distance_m != null) {
        const distanceText = data.distance_m < 1000
            ? `${data.distance_m} ${plural(data.distance_m, ['метр', 'метра', 'метров'])}`
            : `${data.distance_km} км`;
        missBlock.innerHTML = 'Ты промахнулся на <span class="miss-distance" id="result-distance"></span> от точки съёмки';
        document.getElementById('result-distance').textContent = distanceText;
    } else {
        missBlock.textContent = 'Время вышло — точка так и не была отмечена';
    }

    // Очки за раунд
    document.getElementById('result-score').textContent = data.score;
    document.getElementById('result-score-word').textContent =
        plural(data.score, ['очко', 'очка', 'очков']);

    // Заголовок результата
    const title = document.getElementById('result-title');
    if (data.timed_out && !data.guess) {
        title.textContent = 'Время вышло!';
    } else if (data.score >= 4500) {
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
 * Обратное геокодирование на клиенте (JS API v2, ymaps.geocode).
 * Возвращает «улица, дом» или null — фолбэк, когда сервер без ключа геокодера.
 */
async function clientReverseGeocode(latitude, longitude) {
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
 * Уничтожить основной плеер панорамы (перед новым раундом / после игры)
 */
function destroyPanoramaPlayer() {
    if (panoramaPlayer) {
        try {
            panoramaPlayer.destroy();
        } catch (error) {
            // плеер мог уже умереть вместе с DOM-узлом
        }
        panoramaPlayer = null;
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

    // Раунд без догадки (время вышло): показываем только реальную точку
    if (!data.guess) {
        resultMap = new YMap(mapContainer, {
            location: { center: [correctLon, correctLat], zoom: 14 }
        });
        resultMap.addChild(new YMapDefaultSchemeLayer());
        resultMap.addChild(new YMapDefaultFeaturesLayer());
        resultMap.addChild(new YMapMarker({
            coordinates: [correctLon, correctLat]
        }, createPinElement(PIN_RED)));
        return;
    }

    const guessLon = data.guess.longitude;
    const guessLat = data.guess.latitude;

    // Сами вписываем обе точки в кадр. Панель результата закрывает часть
    // карты (справа на десктопе, снизу на мобильной раскладке) — учитываем
    // это и в масштабе, и в центре.
    const isMobile = window.innerWidth <= 720;
    const stageW = window.innerWidth;
    const stageH = window.innerHeight;
    const panelRight = isMobile ? 0 : Math.min(380, stageW);
    const panelBottom = isMobile ? stageH * 0.62 : 0;
    const padPx = 70;

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
    destroyPanoramaPlayer();
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

                    const distanceText = round.distance_m == null
                        ? '<span class="round-timeout">время вышло</span>'
                        : (round.distance_m < 1000
                            ? `${round.distance_m} м`
                            : `${(round.distance_m / 1000).toFixed(1)} км`);

                    const roundLabel = round.address
                        ? `${round.round}. ${escapeHtml(round.address)}`
                        : `Раунд ${round.round}`;

                    item.innerHTML = `
                        <span class="round-name">${roundLabel}</span>
                        <span class="round-distance">${distanceText}</span>
                        <span class="round-score ${getScoreClass(round.score)}">${round.score}</span>
                    `;
                    summaryContainer.appendChild(item);
                });
            }

            showChallengeCompare(data.challenge);
            showChallengeButton(data.challenge_token);
            showScreen('final-screen');

            // Карта и статистика — после показа экрана (карте нужен размер контейнера)
            renderFinalMap(data.rounds || []);
            loadPlayerStats(data.player_name);
        }
    } catch (error) {
        console.error('Ошибка получения результатов:', error);
        showScreen('final-screen');
    }
}

/**
 * Блок сравнения с автором челленджа на финальном экране
 */
function showChallengeCompare(challenge) {
    const block = document.getElementById('challenge-compare');
    if (!challenge) {
        block.classList.add('hidden');
        return;
    }

    let verdict;
    if (challenge.your_score > challenge.opponent_score) {
        verdict = '🏆 Ты победил!';
    } else if (challenge.your_score < challenge.opponent_score) {
        verdict = 'Соперник оказался точнее';
    } else {
        verdict = '🤝 Ничья!';
    }

    block.innerHTML = `
        <div class="cc-verdict">${verdict}</div>
        <div class="cc-scores">
            <span>Ты: <strong>${challenge.your_score}</strong></span>
            <span>${escapeHtml(challenge.opponent_name)}: <strong>${challenge.opponent_score}</strong></span>
        </div>
    `;
    block.classList.remove('hidden');
}

/**
 * Кнопка «Бросить вызов другу»: копирует ссылку на игру с теми же точками
 */
function showChallengeButton(token) {
    const btn = document.getElementById('challenge-btn');
    if (!token) {
        btn.classList.add('hidden');
        return;
    }
    btn.dataset.token = token;
    btn.classList.remove('hidden');
}

async function copyChallengeLink() {
    const token = document.getElementById('challenge-btn').dataset.token;
    if (!token) return;
    const link = `${window.location.origin}/?challenge=${encodeURIComponent(token)}`;

    try {
        await navigator.clipboard.writeText(link);
        showToast('Ссылка скопирована — отправь её другу!');
    } catch (error) {
        // Clipboard API недоступен (например, http без localhost) — показываем ссылку
        window.prompt('Скопируй ссылку-вызов:', link);
    }
}

/**
 * Всплывающее уведомление внизу экрана
 */
let toastTimeout = null;
function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.classList.remove('hidden');
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => toast.classList.add('hidden'), 3000);
}

/**
 * Статистика игрока по имени на финальном экране
 */
async function loadPlayerStats(playerName) {
    const block = document.getElementById('player-stats');
    block.classList.add('hidden');
    if (!playerName) return;

    try {
        const response = await fetch(`/api/player/stats?name=${encodeURIComponent(playerName)}`, {
            credentials: 'same-origin'
        });
        if (!response.ok) return;
        const stats = await response.json();
        // Одна игра — статистика ещё ни о чём не говорит
        if (!stats.games || stats.games < 2) return;

        block.innerHTML = `${escapeHtml(playerName)}: игр — <strong>${stats.games}</strong>, ` +
            `лучший счёт — <strong>${stats.best_score}</strong>, ` +
            `средний — <strong>${stats.avg_score}</strong>`;
        block.classList.remove('hidden');
    } catch (error) {
        console.error('Ошибка загрузки статистики:', error);
    }
}

/**
 * Карта всех раундов игры на финальном экране:
 * синие пины — догадки, красные — реальные точки, пунктир — промахи.
 */
async function renderFinalMap(rounds) {
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

        finalMap = new YMap(container, {
            location: { center: [centerLon, centerLat], zoom }
        });
        finalMap.addChild(new YMapDefaultSchemeLayer());
        finalMap.addChild(new YMapDefaultFeaturesLayer());

        rounds.forEach(r => {
            if (r.guess && r.actual) {
                finalMap.addChild(new YMapFeature({
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
                finalMap.addChild(new YMapMarker({
                    coordinates: [r.actual.longitude, r.actual.latitude]
                }, createPinElement(PIN_RED)));
            }
            if (r.guess) {
                finalMap.addChild(new YMapMarker({
                    coordinates: [r.guess.longitude, r.guess.latitude]
                }, createPinElement(PIN_NAVY)));
            }
        });
    } catch (error) {
        console.error('Не удалось построить карту раундов:', error);
        container.classList.add('hidden');
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

                // Игры с лимитом времени помечаем: их счёт «дороже»
                const timerBadge = entry.time_limit
                    ? `<span class="lb-badge lb-badge-timer">⏱ ${entry.time_limit >= 60
                        ? Math.round(entry.time_limit / 60) + ' мин'
                        : entry.time_limit + ' с'}</span>`
                    : '';

                row.innerHTML = `
                    <span class="leaderboard-rank ${rankClass}">${entry.rank}</span>
                    <span class="leaderboard-name">${escapeHtml(entry.player_name)}${badge}${timerBadge}</span>
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
