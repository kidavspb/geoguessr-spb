/**
 * Хэллоуин (временно): «Ночь Медного всадника».
 *
 * Погоня — обычная игра с таймером на 1 или 3 минуты: правила, счёт и
 * таблица лидеров те же. Модуль только оформляет её. На стартовом экране из-за
 * логотипа то появляется, то исчезает Медный всадник; клик по нему — волна на
 * весь экран, и тот же экран переодевается в палитру обложки: «ночной»
 * логотип, название, легенда, коралловый старт, вызов дня и «Без лимита»
 * неактивны. Повторный клик по всаднику возвращает обычную игру. В игре отсчёт
 * становится погоней: всадник приближается, панорама чёрно-белая, края
 * темнеют, в последние секунды стучит сердце, когда время вышло — «Попался!».
 * Все слои — CSS поверх панорамы с pointer-events: none; второго Player или
 * карты режим не создаёт. Разметка есть только в сезон (см. halloween.py),
 * без неё функции ничего не делают. Режим не запоминается: каждый визит
 * всадника находят заново.
 */

export const CHASE_TIME_LIMIT = 60;

// Тот же порог, что у красного таймера в main.js.
const CLOSE_MS = 10000;
// Волна входа в режим (CSS: 1.4s): переключаем, пока экран закрыт.
const FLOOD_SWITCH_MS = 700;
const FLOOD_MS = 1450;
const WHISPER_MS = 3200;
const CAUGHT_MS = 1800;

const WHISPERS = [
    'Медный всадник сошёл с Гром-камня…',
    '«Тяжело-звонкое скаканье по потрясённой мостовой»',
    'Он прямо за спиной — отмечай точку!',
];

let chaseMode = false;
let whisperStage = 0;
let whisperTimer = null;
let caughtTimer = null;
let caught = false;

/** Включён ли режим погони на стартовом экране. */
export function chaseModeOn() {
    return chaseMode;
}

/** Всадник за логотипом; onModeChange(on) сообщает о смене режима. */
export function initHalloween({ onModeChange }) {
    const peek = document.getElementById('horseman-peek');
    if (!peek) return;

    // Тот же всадник и выключает режим — без отдельной кнопки.
    peek.addEventListener('click', () => {
        const on = !chaseMode;
        runFlood(() => {
            setChaseMode(on);
            onModeChange(on);
            if (on) document.getElementById('chase-title').focus();
        });
    });
}

/** Волна на весь экран; switchMode срабатывает, пока экран закрыт. */
function runFlood(switchMode) {
    const flood = document.getElementById('chase-flood');
    if (flood.classList.contains('run')) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        switchMode();
        return;
    }
    flood.classList.add('run');
    setTimeout(switchMode, FLOOD_SWITCH_MS);
    setTimeout(() => flood.classList.remove('run'), FLOOD_MS);
}

function setChaseMode(on) {
    chaseMode = on;
    document.getElementById('start-screen').classList.toggle('is-chase', on);
    document.documentElement.classList.toggle('theme-chase', on);
    document.getElementById('horseman-peek').setAttribute('aria-label', on
        ? 'Медный всадник. Вернуться к обычной игре'
        : 'Медный всадник. Включить «Ночь Медного всадника»');
    document.getElementById('start-btn').textContent = on ? 'Бежать' : 'Начать игру';
    // Вызов дня без таймера: в погоне он виден, но недоступен.
    document.getElementById('daily-btn').disabled = on;
}

/** Игровой экран в оформлении погони (ч/б панорама, таймер) или обычном. */
export function setChaseGame(on) {
    document.querySelector('.game-stage').classList.toggle('chase-mode', on);
}

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
    // Картинка сцены — та же, что в погоне: к этому моменту она уже в кэше.
    const image = overlay.querySelector('img');
    if (!image.getAttribute('src')) {
        image.src = document.getElementById('chase-horseman').src;
    }
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
