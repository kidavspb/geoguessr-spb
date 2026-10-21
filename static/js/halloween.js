/**
 * Хэллоуин (временно): «Побег от Медного всадника».
 *
 * Это обычная игра с минутным таймером — правила, счёт и таблица лидеров те
 * же. Модуль только рисует отсчёт как погоню: всадник приближается, края
 * темнеют, в последние секунды стучит сердце, а когда время вышло, мелькает
 * сцена «Попался!». Все слои — CSS поверх панорамы с pointer-events: none;
 * второго Player или карты режим не создаёт. Разметка есть только в сезон
 * (см. halloween.py), без неё функции ничего не делают.
 */

export const CHASE_TIME_LIMIT = 60;

// Тот же порог, что у красного таймера в main.js.
const CLOSE_MS = 10000;
const WHISPER_MS = 3200;
const CAUGHT_MS = 1800;

const WHISPERS = [
    'Медный всадник сошёл с Гром-камня…',
    '«Тяжело-звонкое скаканье по потрясённой мостовой»',
    'Он прямо за спиной — отмечай точку!',
];

let whisperStage = 0;
let whisperTimer = null;
let caughtTimer = null;
let caught = false;

/** Новый раунд или новая игра: всадник снова далеко. */
export function resetChase() {
    whisperStage = 0;
    caught = false;
    document.getElementById('chase-layer')?.classList.add('hidden');
    hideWhisper();
}

/** Кадр погони по остатку времени; вызывается каждым тиком таймера. */
export function updateChase(remainingMs, totalMs) {
    const layer = document.getElementById('chase-layer');
    if (!layer) return;
    const progress = Math.min(1, Math.max(0, 1 - remainingMs / totalMs));
    layer.style.setProperty('--chase-p', progress.toFixed(3));
    layer.classList.toggle('chase-close', remainingMs <= CLOSE_MS);
    layer.classList.remove('hidden');

    // Фразы идут только вперёд: повторный старт таймера по ответу /ready
    // не должен заново показывать уже прозвучавшую.
    const stage = remainingMs <= CLOSE_MS ? 3 : progress >= 0.5 ? 2 : 1;
    if (stage > whisperStage) {
        whisperStage = stage;
        showWhisper(WHISPERS[stage - 1]);
    }
}

/** Время вышло: короткая сцена поверх всего, ответ уходит параллельно. */
export function chaseCaught() {
    const overlay = document.getElementById('chase-caught');
    if (!overlay) return;
    caught = true;
    hideWhisper();
    clearTimeout(caughtTimer);
    overlay.classList.add('show');
    caughtTimer = setTimeout(() => overlay.classList.remove('show'), CAUGHT_MS);
    try {
        navigator.vibrate?.([90, 60, 180]);
    } catch (e) {
        // вибрация — необязательное украшение
    }
}

/** Закончился ли текущий раунд поимкой — для заголовка результата. */
export function wasCaught() {
    return caught;
}

function showWhisper(text) {
    const whisper = document.getElementById('chase-whisper');
    if (!whisper) return;
    clearTimeout(whisperTimer);
    whisper.textContent = text;
    whisper.classList.add('show');
    whisperTimer = setTimeout(() => whisper.classList.remove('show'), WHISPER_MS);
}

function hideWhisper() {
    clearTimeout(whisperTimer);
    document.getElementById('chase-whisper')?.classList.remove('show');
}
