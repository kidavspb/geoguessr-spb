/**
 * Общее состояние игры и константы.
 * Единственный источник правды для всех модулей.
 */

// Координаты центра СПб и стартовый зум карты выбора
export const SPB_CENTER = [59.9311, 30.3609];
export const DEFAULT_ZOOM = 11;

// Фирменные пины (из логотипа): красный — реальная точка, синий — догадка игрока
export const PIN_RED = '/static/img/pin.svg';
export const PIN_NAVY = '/static/img/pin-navy.svg';

// Сколько раз перегенерировать точку, если панорамы нет (сервер лимитирует жёстче)
export const MAX_PANORAMA_RETRIES = 8;

// Описания сложности на стартовом экране
export const DIFFICULTY_HINTS = {
    'center': 'Центр — исторический центр города',
    'medium': 'Средняя — центр и ближайшие районы',
    'hard': 'Весь город — от центра до окраин'
};

// Мутабельное состояние. Модули меняют поля напрямую — приложение маленькое,
// и это проще, чем шина событий.
export const state = {
    map: null,             // карта выбора (v3)
    resultMap: null,       // карта результата раунда (v3)
    finalMap: null,        // карта всех раундов на финальном экране (v3)
    panoramaPlayer: null,  // основной плеер панорамы (v2)
    lastPanorama: null,    // панорама текущего раунда — для круга на экране результата
    resultPanoPlayer: null,// мини-плеер в круге
    currentMarker: null,   // маркер догадки на карте выбора
    guessCoords: null,     // текущая догадка {latitude, longitude}
    panoramaRetries: 0,    // сколько раз перегенерировали точку в этом раунде
    panoStartPoint: null,  // стартовая точка панорамы (кнопка «к началу», no-move)
    preloaded: null,       // предзагруженный раунд {round, latitude, longitude, panorama, player, stagingDiv}
    noMoveWatchdog: null,  // интервал-страховка режима «без перемещения»
    modalPlayer: null,     // плеер полноэкранного просмотра панорамы
    roundTimerInterval: null,
    roundDeadline: null,   // момент окончания времени раунда (ms)
    guessSubmitting: false,// защита от двойной отправки (клик + таймер)
    roundLoading: false,   // защита от параллельной загрузки раунда
    overlayTimer: null,    // отложенный показ оверлея «Загрузка…»
    noMoveWarned: false,   // тост «без перемещения» показан в этом раунде

    gameData: {
        totalRounds: 5,
        currentRound: 1,
        totalScore: 0,
        difficulty: 'medium', // center, medium, hard, hardcore
        timeLimit: 0,         // секунд на раунд, 0 — без лимита
        noMove: false,        // режим «без перемещения»
        challengeToken: null, // токен челленджа из ссылки-вызова
        daily: false          // игра — ежедневный вызов
    },

    // Фильтры таблицы лидеров
    lbState: { difficulty: 'all', period: 'all' }
};
