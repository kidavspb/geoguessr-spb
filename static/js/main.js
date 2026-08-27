/**
 * Петербургский следопыт — точка входа.
 * Экраны, ход игры, таймер, лидерборд, вызов дня, челленджи.
 */
import { state, DIFFICULTY_HINTS } from './state.js';
import { plural, escapeHtml, getScoreClass, showScreen, showToast, reachGoal, randomPlayerName } from './utils.js';
import { api } from './api.js';
import {
    loadPanorama, prefetchNextRound, discardPreloaded, returnToPanoStart,
    showResultPano, destroyResultPano, destroyPanoramaPlayer,
    openPanoModal, closePanoModal, clientReverseGeocode,
    scheduleLoadingOverlay, showLoadingOverlay
} from './panorama.js';
import {
    initMap, toggleMapPanel, resetMapForNewRound, showResultMap, renderFinalMap,
    destroyMainMap, destroyResultMap, destroyFinalMap
} from './maps.js';

const { gameData } = state;
const TERRITORIES = ['center', 'medium', 'hard'];
const TERRITORY_NAMES = ['Центр', 'Средняя', 'Весь город'];

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    initPlayerName();
    initEventListeners();
    initToggles();
    initChallengeFromUrl();
    initDailyButton();
    checkYandexMapsAPI();
});

/**
 * Имя игрока: сохранённое, а новому игроку — случайное петербургское.
 * Так результаты разных людей не сливаются в общего «Анонима».
 */
function initPlayerName() {
    const input = document.getElementById('player-name');
    let name = null;
    try {
        name = localStorage.getItem('playerName');
    } catch (e) {
        // приватный режим без localStorage — просто сгенерируем на раз
    }
    input.value = name || randomPlayerName();
}

/**
 * Обработчики событий всех экранов
 */
function initEventListeners() {
    // Стартовый экран
    document.getElementById('start-btn').addEventListener('click', () => startGame());
    document.getElementById('daily-btn').addEventListener('click', () => startGame({ daily: true }));
    document.getElementById('show-leaderboard-btn').addEventListener('click', openLeaderboard);

    // Игровой экран
    document.getElementById('guess-btn').addEventListener('click', submitGuess);
    document.getElementById('map-handle').addEventListener('click', toggleMapPanel);
    document.getElementById('pano-home').addEventListener('click', returnToPanoStart);
    document.getElementById('retry-panorama-btn').addEventListener('click', retryPanorama);
    document.getElementById('skip-panorama-btn').addEventListener('click', skipPanorama);

    // Экран результата. ВАЖНО: обработчик кнопки «Продолжить» назначается
    // только через onclick в showRoundResult — addEventListener здесь дал бы
    // ВТОРОЙ вызов nextRound на клик и гонку двух загрузок панорамы.
    document.getElementById('result-pano').addEventListener('click', openPanoModal);
    document.getElementById('pano-modal-close').addEventListener('click', closePanoModal);

    // Финальный экран
    document.getElementById('play-again-btn').addEventListener('click', () => {
        discardPreloaded(state.preloaded);
        state.preloaded = null;
        closePanoModal();
        destroyMainMap();
        destroyFinalMap();
        showScreen('start-screen');
    });
    document.getElementById('final-leaderboard-btn').addEventListener('click', openLeaderboard);
    document.getElementById('challenge-btn').addEventListener('click', shareChallengeLink);

    // Фильтры таблицы лидеров: период и сложность — независимые оси
    document.querySelectorAll('.lb-period-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            state.lbState.period = btn.dataset.period;
            syncLeaderboardFilters();
            showLeaderboard();
        });
    });
    document.querySelectorAll('#leaderboard-filter .lb-filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            state.lbState.difficulty = btn.dataset.difficulty;
            syncLeaderboardFilters();
            showLeaderboard();
        });
    });

    document.getElementById('back-btn').addEventListener('click', () => {
        state.leaderboardLoadId++;
        destroyFinalMap();
        showScreen('start-screen');
    });

    // Enter для начала игры
    document.getElementById('player-name').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') startGame();
    });
}

/** Настройки стартового экрана используют нативные controls, а gameData
 * остаётся единым состоянием, которое затем отправляется на сервер. */
function initToggles() {
    const territoryRange = document.getElementById('territory-range');
    territoryRange.addEventListener('input', () => {
        const index = Math.max(0, Math.min(TERRITORIES.length - 1,
            Number.parseInt(territoryRange.value, 10) || 0));
        gameData.difficulty = TERRITORIES[index];
        syncSettingsControls();
    });

    document.querySelectorAll('.territory-label').forEach(label => {
        label.addEventListener('click', () => {
            territoryRange.value = label.dataset.territoryIndex;
            territoryRange.dispatchEvent(new Event('input', { bubbles: true }));
        });
    });

    document.querySelectorAll('input[name="round-time"]').forEach(input => {
        input.addEventListener('change', () => {
            if (!input.checked) return;
            gameData.timeLimit = Number.parseInt(input.value, 10) || 0;
        });
    });

    document.getElementById('movement-toggle').addEventListener('change', event => {
        gameData.noMove = !event.target.checked;
        syncSettingsControls();
    });

    syncSettingsControls();
}

function syncSettingsControls() {
    const territoryIndex = Math.max(0, TERRITORIES.indexOf(gameData.difficulty));
    const territoryRange = document.getElementById('territory-range');
    const territoryControl = document.getElementById('territory-control');
    territoryRange.value = String(territoryIndex);
    territoryRange.setAttribute('aria-valuetext', TERRITORY_NAMES[territoryIndex]);
    territoryControl.style.setProperty('--territory-position', `${territoryIndex * 50}%`);
    document.querySelectorAll('.territory-label').forEach((label, index) => {
        label.setAttribute('aria-pressed', String(index === territoryIndex));
    });
    document.getElementById('territory-hint').textContent = DIFFICULTY_HINTS[gameData.difficulty];

    const timerValue = String(gameData.timeLimit || 0);
    document.querySelectorAll('input[name="round-time"]').forEach(input => {
        input.checked = input.value === timerValue;
    });

    const movementToggle = document.getElementById('movement-toggle');
    movementToggle.checked = !gameData.noMove;
    document.getElementById('movement-description').textContent = gameData.noMove
        ? 'Начальная точка обзора будет зафиксирована'
        : 'Свободно перемещайтесь по панораме';
}

/**
 * Кнопка «Вызов дня»: показывает, сыграл ли уже игрок и сколько человек
 * прошло сегодняшний набор точек.
 */
async function initDailyButton() {
    const hint = document.getElementById('daily-hint');
    try {
        const { ok, data } = await api.dailyInfo();
        if (!ok) return;

        const parts = [];
        if (data.played) {
            parts.push(`Ты уже сыграл: ${data.your_score} ${plural(data.your_score, ['очко', 'очка', 'очков'])}`);
        }
        if (data.players_today > 0) {
            parts.push(`сегодня сыграли: ${data.players_today}`);
        }
        if (parts.length) {
            hint.textContent = parts.join(' · ');
            hint.classList.remove('hidden');
        }
    } catch (error) {
        console.error('Ошибка загрузки вызова дня:', error);
    }
}

/**
 * Заход по ссылке-вызову (?challenge=TOKEN): показываем баннер с данными
 * автора челленджа; параметры игры берутся из его партии.
 */
async function initChallengeFromUrl() {
    const token = new URLSearchParams(window.location.search).get('challenge');
    if (!token) return;
    // Токен известен из URL сразу: быстрый клик «Начать» не должен случайно
    // запустить обычную игру, пока декоративный запрос данных соперника летит.
    gameData.challengeToken = token;

    const banner = document.getElementById('challenge-banner');
    try {
        const { ok, data } = await api.challengeInfo(token);
        if (gameData.challengeToken !== token) return;

        if (!ok) {
            gameData.challengeToken = null;
            banner.textContent = 'Челлендж не найден — можно сыграть обычную игру.';
            banner.classList.remove('hidden');
            return;
        }
        const extras = [];
        if (data.time_limit) extras.push(`${Math.round(data.time_limit / 60) || 1} мин на раунд`);
        if (data.no_move) extras.push('не сходя с места');
        const extraText = extras.length ? `, ${extras.join(', ')}` : '';
        banner.innerHTML = `⚔️ <strong>${escapeHtml(data.player_name)}</strong> бросает тебе вызов!<br>` +
            `Счёт: <strong>${data.total_score}</strong> ` +
            `(${escapeHtml(data.difficulty_name || '')}${extraText}). Те же точки — сможешь лучше?`;
        banner.classList.remove('hidden');

        // Параметры фиксированы челленджем — селекторы прячем
        document.getElementById('difficulty-group').classList.add('hidden');
        document.getElementById('timer-group').classList.add('hidden');
        document.getElementById('move-group').classList.add('hidden');
    } catch (error) {
        console.error('Ошибка загрузки челленджа:', error);
    }
}

/**
 * Предупреждение, если Яндекс.Карты не загрузились
 */
function checkYandexMapsAPI() {
    setTimeout(() => {
        const errors = window.yandexMapsLoadErrors || {};
        if (errors.v2 || errors.v3) {
            console.error('Один из API Яндекс.Карт не загрузился');
            const startBtn = document.getElementById('start-btn');
            if (startBtn && !document.querySelector('.api-warning')) {
                const warning = document.createElement('div');
                warning.className = 'api-warning';
                warning.textContent = '⚠️ Карты пока не загрузились. Можно попробовать начать — приложение повторит подключение.';
                startBtn.parentElement.insertBefore(warning, startBtn);
            }
        }
    }, 10000);
}

/**
 * Начало новой игры
 */
async function startGame(opts = {}) {
    if (state.gameStarting) return;
    state.gameStarting = true;
    stopRoundTimer();
    document.getElementById('start-btn').disabled = true;
    document.getElementById('daily-btn').disabled = true;
    const playerName = document.getElementById('player-name').value.trim() || randomPlayerName();
    document.getElementById('player-name').value = playerName;
    try {
        localStorage.setItem('playerName', playerName);
    } catch (e) {
        // localStorage недоступен — не страшно
    }

    try {
        const { ok, status, data } = await api.startGame({
            player_name: playerName,
            difficulty: gameData.difficulty,
            time_limit: gameData.timeLimit || null,
            no_move: gameData.noMove,
            challenge_token: gameData.challengeToken,
            daily: !!opts.daily
        });

        // Вызов дня уже сыгран сегодня — показываем счёт и топ дня
        if (status === 409 && data && data.already_played) {
            showToast(`Сегодня ты уже играл: ${data.total_score} ${plural(data.total_score, ['очко', 'очка', 'очков'])}`);
            state.lbState.period = 'daily';
            syncLeaderboardFilters();
            showLeaderboard();
            return;
        }

        if (!ok) {
            alert('Ошибка: ' + (data ? data.error : status));
            return;
        }

        gameData.totalRounds = data.total_rounds;
        gameData.currentRound = 1;
        gameData.totalScore = data.total_score || 0; // >0 при возврате в недоигранный вызов дня
        gameData.difficulty = data.difficulty || gameData.difficulty;
        gameData.timeLimit = data.time_limit || 0;   // серверные значения — истина
        gameData.noMove = !!data.no_move;
        gameData.daily = !!data.daily;
        syncSettingsControls();
        discardPreloaded(state.preloaded);
        state.preloaded = null;
        state.currentLocation = null;
        state.currentRoundId = null;
        state.actualPoint = null;
        state.lastPanorama = null;
        state.panoStartPoint = null;
        destroyPanoramaPlayer();
        closePanoModal();
        destroyMainMap();
        destroyResultMap();
        destroyFinalMap();

        // Метрика: какие режимы реально играют
        reachGoal(gameData.daily ? 'daily_start'
                : gameData.challengeToken ? 'challenge_accept' : 'game_start');

        // Челлендж одноразовый: следующая игра с этими же точками не имеет
        // смысла — игрок их уже видел. Возвращаем обычный стартовый экран.
        if (gameData.challengeToken) {
            gameData.challengeToken = null;
            document.getElementById('challenge-banner').classList.add('hidden');
            document.getElementById('difficulty-group').classList.remove('hidden');
            document.getElementById('timer-group').classList.remove('hidden');
            document.getElementById('move-group').classList.remove('hidden');
            window.history.replaceState(null, '', window.location.pathname);
        }

        document.getElementById('total-rounds').textContent = gameData.totalRounds;
        document.getElementById('total-score').textContent = String(gameData.totalScore);

        showScreen('game-screen');

        // Панорама — главный контент раунда. Она начинает грузиться сразу, а
        // карта выбора инициализируется параллельно и не держит первый кадр.
        const roundPromise = loadCurrentLocation(data.location || null);
        initGuessMapWithRecovery();
        await roundPromise;
    } catch (error) {
        console.error('Ошибка запуска игры:', error);
        alert('Не удалось начать игру. Проверьте подключение. Детали: ' + error.message);
    } finally {
        state.gameStarting = false;
        document.getElementById('start-btn').disabled = false;
        document.getElementById('daily-btn').disabled = false;
    }
}

async function initGuessMapWithRecovery() {
    const hint = document.getElementById('map-hint');
    try {
        const initialized = await initMap();
        if (!initialized) return;
        resetMapForNewRound();
    } catch (mapError) {
        if (!document.getElementById('game-screen').classList.contains('active')) return;
        console.error('Ошибка инициализации карты:', mapError);
        hint.textContent = 'Карта не загрузилась — нажмите, чтобы повторить';
        hint.classList.remove('hidden');
        hint.classList.add('map-retry');
        hint.onclick = () => initGuessMapWithRecovery();
        showToast('Панорама работает; карту выбора можно повторно загрузить отдельно');
    }
}

/**
 * Загрузка текущего раунда (панорама + сброс карты + таймер)
 */
async function loadCurrentLocation(locationOverride = null) {
    // Защита от параллельного запуска (двойной клик, поспешный повтор):
    // две одновременные загрузки панорамы оставляют пустой экран
    if (state.roundLoading) return;
    state.roundLoading = true;
    state.roundInteractive = false;
    document.getElementById('guess-btn').disabled = true;

    // Оверлей загрузки появится, только если раунд грузится дольше 300 мс:
    // прогретый раунд подключается мгновенно, без мелькания «Загрузки»
    scheduleLoadingOverlay();

    try {
        let data = locationOverride;
        const preload = state.preloaded;

        // После результата координаты следующего раунда уже лежат в единой
        // preload-задаче. Не делаем второй GET и не запускаем второй locate.
        if (!data && preload && preload.location) data = preload.location;
        if (!data) {
            const response = await api.getLocation();
            data = response.data;

            if (data && data.game_over) {
                showFinalResults();
                return;
            }
            if (!response.ok) {
                showLoadingOverlay(data && data.error ? data.error : 'Сервер не ответил',
                                   { retry: true });
                return;
            }
        }

        if (data && data.game_over) {
            showFinalResults();
            return;
        }
        if (!data || !data.round_id) {
            showLoadingOverlay('Сервер вернул неполные данные раунда.', { retry: true });
            return;
        }

        gameData.currentRound = data.round;
        state.currentLocation = data;
        state.currentRoundId = data.round_id;
        document.getElementById('current-round').textContent = data.round;

        // «Вернуться к началу» бессмысленна, когда ходить нельзя
        document.getElementById('pano-home').classList.toggle('hidden', !!gameData.noMove);

        resetMapForNewRound();
        const matchingPreload = preload && preload.roundId === data.round_id
            ? preload : null;
        if (preload && !matchingPreload) {
            discardPreloaded(preload);
            state.preloaded = null;
        }

        const loaded = await loadPanorama(data, matchingPreload);
        if (!loaded.ok) {
            if (loaded.location) state.currentLocation = loaded.location;
            return;
        }

        state.currentLocation = loaded.location;
        state.currentRoundId = loaded.location.round_id;
        state.roundInteractive = true;
        document.getElementById('guess-btn').disabled = !state.guessCoords;
        // Первый кадр уже нарисован: интерфейс и локальный отсчёт доступны
        // немедленно, даже если подтверждение сервера идёт по плохой сети.
        startRoundTimer(loaded.deadlineMs);
        if (loaded.readyPromise) {
            loaded.readyPromise.then(ready => {
                const serverDeadline = ready && ready.ok && ready.data
                    ? ready.data.deadline_ms : null;
                if (serverDeadline && loaded.loadId === state.roundLoadId &&
                        state.currentRoundId === loaded.location.round_id &&
                        document.getElementById('game-screen').classList.contains('active')) {
                    startRoundTimer(serverDeadline);
                }
            });
        }
    } catch (error) {
        console.error('Ошибка загрузки локации:', error);
        showLoadingOverlay('Ошибка загрузки', { retry: true });
    } finally {
        state.roundLoading = false;
    }
}

function retryPanorama() {
    if (!state.currentLocation || state.roundLoading) return;
    loadCurrentLocation(state.currentLocation);
}

async function skipPanorama() {
    if (!state.currentRoundId || state.roundLoading) return;
    state.roundLoading = true;
    showLoadingOverlay('Выбираем другое место…');
    let skipped;
    try {
        skipped = await api.skipLocation(
            state.currentRoundId,
            'manual_retry',
            state.currentLocation ? state.currentLocation.location_version : null
        );
    } finally {
        state.roundLoading = false;
    }
    if (!skipped || !skipped.ok || !skipped.data) {
        const message = skipped && skipped.data && skipped.data.error
            ? skipped.data.error : 'Не получилось сменить место.';
        showLoadingOverlay(message, {
            retry: true,
            skip: !skipped || skipped.status !== 429
        });
        return;
    }
    state.currentLocation = skipped.data;
    loadCurrentLocation(skipped.data);
}

// --------------------------------------------------------------------------
// Таймер раунда
// --------------------------------------------------------------------------

function startRoundTimer(serverDeadlineMs = null) {
    stopRoundTimer();
    const chip = document.getElementById('timer-chip');
    if (!gameData.timeLimit) {
        state.roundDeadline = null;
        state.lastTimerSecond = null;
        chip.classList.add('hidden');
        return;
    }

    state.roundDeadline = serverDeadlineMs || (Date.now() + gameData.timeLimit * 1000);
    state.lastTimerSecond = null;
    chip.classList.remove('hidden', 'timer-low');
    updateTimerDisplay();
    state.roundTimerInterval = setInterval(updateTimerDisplay, 250);
}

function updateTimerDisplay() {
    const chip = document.getElementById('timer-chip');
    const secondsLeft = Math.max(0, Math.ceil((state.roundDeadline - Date.now()) / 1000));

    if (secondsLeft !== state.lastTimerSecond) {
        state.lastTimerSecond = secondsLeft;
        const min = Math.floor(secondsLeft / 60);
        const sec = String(secondsLeft % 60).padStart(2, '0');
        document.getElementById('timer-value').textContent = `${min}:${sec}`;
    }

    if (secondsLeft <= 10) chip.classList.add('timer-low');

    if (secondsLeft <= 0) {
        stopRoundTimer();
        onRoundTimeExpired();
    }
}

function stopRoundTimer() {
    if (state.roundTimerInterval) {
        clearInterval(state.roundTimerInterval);
        state.roundTimerInterval = null;
    }
}

/**
 * Время раунда вышло: отправляем ответ с тем, что есть.
 * Если точка отмечена — обычная догадка, нет — раунд завершается с 0 очков.
 */
function onRoundTimeExpired() {
    if (state.guessCoords) {
        submitGuess();
    } else {
        sendGuess({ timed_out: true });
    }
}

// --------------------------------------------------------------------------
// Отправка ответа и результат раунда
// --------------------------------------------------------------------------

function submitGuess() {
    if (!state.roundInteractive) return;
    if (!state.guessCoords) {
        alert('Отметьте точку на карте!');
        return;
    }
    document.getElementById('guess-btn').disabled = true;
    sendGuess(state.guessCoords);
}

/**
 * Единая точка отправки догадки (клик по кнопке или истечение таймера)
 */
async function sendGuess(payload) {
    if (state.guessSubmitting) return;
    state.guessSubmitting = true;
    document.getElementById('guess-btn').disabled = true;
    stopRoundTimer();

    let retryTimedOut = false;
    try {
        const requestPayload = { ...payload, round_id: state.currentRoundId };
        // Проверяемый actual point попадает в ту же транзакцию /guess: счёт
        // всегда считается по той съёмке, которую видел игрок.
        if (state.actualPoint) {
            requestPayload.panorama_latitude = state.actualPoint[0];
            requestPayload.panorama_longitude = state.actualPoint[1];
        }
        const { ok, data, networkError } = await api.guess(requestPayload);
        if (ok) {
            showRoundResult(data);
        } else {
            const message = data && data.error ? data.error : 'Ответ не отправлен';
            showToast(message);
            retryTimedOut = !!payload.timed_out && !!networkError;
            if (!retryTimedOut) {
                document.getElementById('guess-btn').disabled = !state.guessCoords;
                if (gameData.timeLimit && state.roundDeadline > Date.now()) {
                    startRoundTimer(state.roundDeadline);
                }
            }
        }
    } catch (error) {
        console.error('Ошибка отправки ответа:', error);
        showToast('Не удалось отправить ответ');
        document.getElementById('guess-btn').disabled = !state.guessCoords;
    } finally {
        state.guessSubmitting = false;
        if (retryTimedOut) {
            setTimeout(() => sendGuess(payload), 1200);
        }
    }
}

/**
 * Показ результата раунда
 */
function showRoundResult(data) {
    // Основной Player на экране результата не нужен: его WebGL-контекст и
    // текстуры — самое тяжёлое, что есть на странице.
    destroyPanoramaPlayer();
    state.panoStartPoint = null;
    state.actualPoint = null;
    state.roundInteractive = false;
    state.resultRoundId = data.round_id;

    gameData.totalScore = data.total_score;
    document.getElementById('total-score').textContent = data.total_score;

    // Адрес точки («Это было: …»): серверный, а если сервер без ключа
    // геокодера — клиентский, через JS API v2 с обычным ключом карт.
    // Сам адрес — ссылка на панораму этого места в Яндекс Картах.
    const locationBlock = document.getElementById('result-location');
    const addressLink = document.getElementById('correct-location-name');
    addressLink.href = 'https://yandex.ru/maps/?panorama%5Bpoint%5D=' +
        `${data.correct_location.longitude},${data.correct_location.latitude}`;
    if (data.address) {
        addressLink.textContent = data.address;
        locationBlock.classList.remove('hidden');
    } else {
        locationBlock.classList.add('hidden');
        clientReverseGeocode(
            data.correct_location.latitude,
            data.correct_location.longitude
        ).then(address => {
            if (address && state.resultRoundId === data.round_id) {
                addressLink.textContent = address;
                locationBlock.classList.remove('hidden');
                // Геокодирование больше не держит ответ /guess, но адрес
                // сохраняется для финальной сводки и следующих посещений.
                api.setRoundAddress(data.round_id, address);
            }
        });
    }

    // Фактическая сложность места (когда по нему уже накоплена статистика)
    const diffBlock = document.getElementById('result-difficulty');
    if (data.difficulty_percentile != null && data.difficulty_percentile >= 40) {
        diffBlock.textContent = `📊 Это место сложнее ${data.difficulty_percentile}% точек города`;
        diffBlock.classList.remove('hidden');
    } else {
        diffBlock.classList.add('hidden');
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
    nextBtn.disabled = false;
    if (data.is_game_over) {
        reachGoal('game_complete');
        nextBtn.textContent = 'Посмотреть результаты';
        nextBtn.onclick = showFinalResults;
    } else {
        nextBtn.textContent = 'Продолжить';
        nextBtn.onclick = nextRound;
    }

    showScreen('result-screen');

    // Лёгкая кнопка возврата к панораме + карта с результатом.
    showResultPano();
    // Первый paint результата важнее фоновой сети. Сразу после него начинаем
    // единственную подготовку следующего раунда и низкоприоритетный warm-up.
    if (!data.is_game_over && data.next_location) {
        requestAnimationFrame(() => {
            if (state.resultRoundId === data.round_id &&
                    document.getElementById('result-screen').classList.contains('active')) {
                prefetchNextRound(data.next_location);
            }
        });
    }
    showResultMap(data).catch(error => {
        console.error('Не удалось показать карту результата:', error);
        if (state.resultRoundId === data.round_id) {
            showToast('Карта результата не загрузилась, но игру можно продолжить');
        }
    });
}

/**
 * Переход к следующему раунду
 */
function nextRound() {
    const button = document.getElementById('next-round-btn');
    if (button.disabled) return;
    button.disabled = true;
    state.resultRoundId = null;
    closePanoModal();
    destroyResultPano();
    destroyResultMap();
    showScreen('game-screen');
    loadCurrentLocation();
}

// --------------------------------------------------------------------------
// Финальный экран
// --------------------------------------------------------------------------

async function showFinalResults() {
    if (state.finalLoading) return;
    state.finalLoading = true;
    const nextButton = document.getElementById('next-round-btn');
    nextButton.disabled = true;
    destroyResultPano();
    destroyResultMap();
    destroyPanoramaPlayer();
    state.lastPanorama = null;
    state.panoStartPoint = null;
    state.actualPoint = null;
    closePanoModal();
    destroyMainMap();
    state.resultRoundId = null;
    discardPreloaded(state.preloaded);
    state.preloaded = null;
    try {
        const { ok, data } = await api.results();

        if (!ok) {
            showScreen('final-screen');
            return;
        }

        document.getElementById('final-score').textContent = data.total_score;

        // Список раундов
        const summaryContainer = document.getElementById('rounds-summary');
        summaryContainer.innerHTML = '';
        (data.rounds || []).forEach(round => {
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

        showChallengeCompare(data.challenge);
        showChallengeButton(data.challenge_token, data.total_score);
        showScreen('final-screen');

        // Карта и статистика — после показа экрана (карте нужен размер контейнера)
        renderFinalMap(data.rounds || []);
        loadPlayerStats(data.player_name);
        showDailyTop(data.daily);
    } catch (error) {
        console.error('Ошибка получения результатов:', error);
        showScreen('final-screen');
    } finally {
        state.finalLoading = false;
    }
}

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
 * Топ сегодняшнего вызова дня на финальном экране (если игра была daily)
 */
async function showDailyTop(isDaily) {
    const block = document.getElementById('daily-top');
    block.classList.add('hidden');
    if (!isDaily) return;

    try {
        const { ok, data } = await api.dailyLeaderboard();
        if (!ok || !data.leaderboard || !data.leaderboard.length) return;

        const rows = data.leaderboard.slice(0, 5).map(e =>
            `<div class="dt-row"><span>${e.rank}. ${escapeHtml(e.player_name)}</span><strong>${e.total_score}</strong></div>`
        ).join('');
        block.innerHTML = `<div class="dt-title">🗓 Топ вызова дня</div>${rows}`;
        block.classList.remove('hidden');
    } catch (error) {
        console.error('Ошибка загрузки топа дня:', error);
    }
}

function showChallengeButton(token, totalScore) {
    const btn = document.getElementById('challenge-btn');
    if (!token) {
        btn.classList.add('hidden');
        return;
    }
    btn.dataset.token = token;
    btn.dataset.score = totalScore;
    btn.classList.remove('hidden');
}

async function shareChallengeLink() {
    const btn = document.getElementById('challenge-btn');
    const token = btn.dataset.token;
    if (!token) return;
    const link = `${window.location.origin}/?challenge=${encodeURIComponent(token)}`;
    const text = `Я набрал ${btn.dataset.score} очков в «Петербургском следопыте». Тебе те же 5 панорам — сможешь точнее?`;

    reachGoal('challenge_share');

    // На телефонах — системное меню «Поделиться», на десктопе — буфер обмена
    if (navigator.share) {
        try {
            await navigator.share({ title: 'Петербургский следопыт', text, url: link });
            return;
        } catch (error) {
            if (error.name === 'AbortError') return; // пользователь передумал
        }
    }
    try {
        await navigator.clipboard.writeText(`${text}\n${link}`);
        showToast('Ссылка скопирована — отправь её другу!');
    } catch (error) {
        // Clipboard API недоступен (например, http без localhost) — показываем ссылку
        window.prompt('Скопируй ссылку-вызов:', link);
    }
}

/**
 * Статистика игрока по имени на финальном экране
 */
async function loadPlayerStats(playerName) {
    const block = document.getElementById('player-stats');
    block.classList.add('hidden');
    if (!playerName) return;

    try {
        const { ok, data } = await api.playerStats(playerName);
        if (!ok) return;
        // Одна игра — статистика ещё ни о чём не говорит
        if (!data.games || data.games < 2) return;

        block.innerHTML = `${escapeHtml(playerName)}: игр — <strong>${data.games}</strong>, ` +
            `лучший счёт — <strong>${data.best_score}</strong>, ` +
            `средний — <strong>${data.avg_score}</strong>`;
        block.classList.remove('hidden');
    } catch (error) {
        console.error('Ошибка загрузки статистики:', error);
    }
}

// --------------------------------------------------------------------------
// Таблица лидеров
// --------------------------------------------------------------------------

function openLeaderboard() {
    destroyFinalMap();
    state.lbState = { difficulty: 'all', period: 'all' };
    syncLeaderboardFilters();
    document.getElementById('leaderboard-table').innerHTML =
        '<div class="leaderboard-empty">Загрузка…</div>';
    showScreen('leaderboard-screen');
    showLeaderboard();
}

/**
 * Привести кнопки фильтров в соответствие с состоянием.
 * В режиме «День» фильтр сложности не применим (у вызова дня она одна).
 */
function syncLeaderboardFilters() {
    document.querySelectorAll('.lb-period-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.period === state.lbState.period);
    });
    document.querySelectorAll('#leaderboard-filter .lb-filter-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.difficulty === state.lbState.difficulty);
    });
    document.getElementById('leaderboard-filter')
        .classList.toggle('hidden', state.lbState.period === 'daily');
}

/**
 * Показ таблицы лидеров по текущим фильтрам.
 * Период 'daily' — отдельный топ сегодняшнего вызова дня.
 */
async function showLeaderboard() {
    const loadId = ++state.leaderboardLoadId;
    const lbState = { ...state.lbState };
    if (!document.getElementById('leaderboard-screen').classList.contains('active')) {
        document.getElementById('leaderboard-table').innerHTML =
            '<div class="leaderboard-empty">Загрузка…</div>';
        showScreen('leaderboard-screen');
    }

    try {
        let result;
        if (lbState.period === 'daily') {
            result = await api.dailyLeaderboard();
        } else {
            const params = new URLSearchParams();
            if (lbState.difficulty !== 'all') params.set('difficulty', lbState.difficulty);
            if (lbState.period !== 'all') params.set('period', lbState.period);
            result = await api.leaderboard(params);
        }
        if (loadId !== state.leaderboardLoadId) return;
        if (!result.ok) {
            document.getElementById('leaderboard-table').innerHTML =
                '<div class="leaderboard-empty">Не удалось загрузить таблицу</div>';
            return;
        }
        const data = result.data;

        const tableContainer = document.getElementById('leaderboard-table');
        tableContainer.innerHTML = '';

        const showBadge = (lbState.difficulty === 'all' && lbState.period !== 'daily');

        if (data && data.leaderboard && data.leaderboard.length > 0) {
            const header = document.createElement('div');
            header.className = 'leaderboard-row header';
            header.innerHTML = `
                <span class="leaderboard-rank">#</span>
                <span class="leaderboard-name">Игрок</span>
                <span class="leaderboard-score">Очки</span>
                <span class="leaderboard-date">Дата</span>
            `;
            tableContainer.appendChild(header);

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

                // Усложнённые режимы помечаем: их счёт «дороже»
                const timerBadge = entry.time_limit
                    ? `<span class="lb-badge lb-badge-timer">⏱ ${entry.time_limit >= 60
                        ? Math.round(entry.time_limit / 60) + ' мин'
                        : entry.time_limit + ' с'}</span>`
                    : '';
                const noMoveBadge = entry.no_move
                    ? '<span class="lb-badge lb-badge-nomove">🚷 без шагов</span>'
                    : '';

                row.innerHTML = `
                    <span class="leaderboard-rank ${rankClass}">${entry.rank}</span>
                    <span class="leaderboard-name">${escapeHtml(entry.player_name)}${badge}${timerBadge}${noMoveBadge}</span>
                    <span class="leaderboard-score">${entry.total_score}</span>
                    <span class="leaderboard-date">${entry.date || ''}</span>
                `;
                tableContainer.appendChild(row);
            });
        } else {
            tableContainer.innerHTML = '<div class="leaderboard-empty">Пока нет результатов</div>';
        }
    } catch (error) {
        console.error('Ошибка загрузки таблицы лидеров:', error);
        if (loadId === state.leaderboardLoadId) {
            document.getElementById('leaderboard-table').innerHTML =
                '<div class="leaderboard-empty">Не удалось загрузить таблицу</div>';
        }
    }
}
