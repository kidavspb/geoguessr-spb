/**
 * Хэллоуин (временно): звук «Ночи Медного всадника».
 *
 * Только эффекты, без музыки. Волна при входе в режим — шум воды и удар
 * бронзы: всадник сходит с Гром-камня. В раунде — «тяжело-звонкое скаканье
 * по потрясённой мостовой»: галоп сначала далёкий, глухой и редкий, к концу
 * отсчёта громче, звонче и чаще; в последние 10 секунд к нему примешивается
 * сердце. Ответил вовремя — галоп стихает; время вышло — удар, гул бронзы и
 * храп коня вместе со сценой «Попался!».
 *
 * Обычная игра звука не получает и ничего не скачивает: файлы волны грузятся,
 * когда игрок тянется к всаднику (наведение, касание, фокус), остальные — после
 * входа в режим. Звук включён по умолчанию: вход в режим — всегда клик по
 * логотипу, а именно такого жеста браузеры ждут, чтобы разрешить звук. Кнопка
 * в раунде выключает его, выбор запоминается на этом устройстве. На iPhone
 * Web Audio подчиняется переключателю «Без звука». Записи — CC0 с Freesound,
 * источники — static/audio/halloween/README.md.
 */

const STORAGE_KEY = 'halloween-sound';
const ROUND_FILES = ['gallop', 'heartbeat', 'thud', 'gong', 'snort'];

// Шаги галопа лежат в gallop.mp3 по одному на секунду, с тишиной вокруг:
// сдвиг начала, который добавляют кодеры MP3, их не обрезает. Шаги ставятся
// по одному, а не петлёй: темп растёт без смены высоты и без шва петли.
const STRIDE_SLOT = 1;
// Интервал между шагами, с: всадник далеко → вплотную.
const STRIDE_FAR = 0.68;
const STRIDE_NEAR = 0.5;
// Далеко — тихо и глухо (срез высоких), вплотную — громко и звонко.
const GALLOP_GAIN_FAR = 0.12;
const GALLOP_GAIN_NEAR = 0.9;
const CUTOFF_FAR = 600;
const CUTOFF_NEAR = 12000;
// Сердце в последние 10 секунд (тот же порог, что у красного таймера).
const HEART_FROM_MS = 10000;
const HEART_BPM_START = 80;
const HEART_BPM_END = 140;
// Планировщик будит JS раз в 100 мс и ставит звуки на 0,35 с вперёд по часам
// AudioContext — так шаги ровные, даже когда главный поток занят.
const SCHEDULE_MS = 100;
const LOOKAHEAD = 0.35;
// Тики таймера раунда не приходят дольше — погоня кончилась, галоп стихает.
const STALE_MS = 1500;
// Смена режима под волной (FLOOD_SWITCH_MS в halloween.js) — удар бронзы.
const FLOOD_THUD_AT = 0.7;
// Опоздавший шум воды (файл ещё грузился) не догоняет уже прошедшую волну.
const FLOOD_LATE = 0.6;

let soundOn = readPreference();
let base = '';
let version = '';
let ctx = null;
let master = null;
let chase = null;
const loading = new Map();   // имя → Promise<ArrayBuffer>
const decoding = new Map();  // имя → Promise<AudioBuffer | null>
const buffers = new Map();   // имя → AudioBuffer

/** Кнопка звука в раунде, предзагрузка волны и пауза в фоне. */
export function initSound() {
    const button = document.getElementById('chase-sound-btn');
    if (!button) return;
    base = button.dataset.soundBase;
    version = button.dataset.soundVersion;
    syncButton(button);
    button.addEventListener('click', () => {
        soundOn = !soundOn;
        savePreference();
        syncButton(button);
        if (!soundOn) {
            stopChase(true);
            if (master) rampTo(master.gain, 0, 0.02);
        } else if (ensureContext()) {
            rampTo(master.gain, 1, 0.02);
            ROUND_FILES.forEach(decode);
        }
    });

    // Файлы волны — когда игрок тянется к всаднику: к клику они уже здесь.
    const prefetch = () => {
        if (soundOn) load('water', 'thud');
    };
    const wrap = document.querySelector('.start-logo-wrap');
    ['pointerenter', 'pointerdown', 'focusin'].forEach(type =>
        wrap?.addEventListener(type, prefetch, { passive: true }));

    document.addEventListener('visibilitychange', () => {
        if (!ctx) return;
        if (document.hidden) ctx.suspend().catch(() => {});
        else if (soundOn) ctx.resume().catch(() => {});
    });
    // iOS приостанавливает звук после звонка или другого приложения, а
    // вернуть его разрешает только жест — любое касание страницы.
    document.addEventListener('pointerdown', () => {
        if (ctx && soundOn && ctx.state !== 'running' && !document.hidden) {
            ctx.resume().catch(() => {});
        }
    }, { capture: true, passive: true });
}

/** Волна смены режима. Вызывать прямо из клика: этот жест разрешает звук. */
export function playFlood(entering) {
    if (!soundOn || !ensureContext()) return;
    const start = ctx.currentTime;
    decode('water').then(buffer => {
        const late = ctx.currentTime - start;
        if (!buffer || late > FLOOD_LATE) return;
        // Опоздавший файл вступает с середины — в такт с волной.
        play('water', ctx.currentTime, { gain: entering ? 1 : 0.55, offset: late });
    });
    if (!entering) return;
    decode('thud').then(buffer => {
        if (!buffer || ctx.currentTime > start + FLOOD_THUD_AT + 0.15) return;
        play('thud', Math.max(ctx.currentTime, start + FLOOD_THUD_AT), { gain: 0.9 });
    });
    // Галоп, сердце и сцена поимки понадобятся в раунде — готовим заранее.
    ROUND_FILES.forEach(decode);
}

/** Тик погони: progress — доля прошедшего времени, remainingMs — остаток. */
export function chaseSound(progress, remainingMs) {
    if (!soundOn || !ctx) return;
    if (!chase) startChase();
    chase.lastTick = performance.now();
    chase.progress = progress;
    chase.heartBpm = remainingMs > HEART_FROM_MS ? 0 : HEART_BPM_START
        + (HEART_BPM_END - HEART_BPM_START) * (1 - Math.max(0, remainingMs) / HEART_FROM_MS);
    const near = Math.pow(progress, 1.6);
    rampTo(chase.gallop.gain, GALLOP_GAIN_FAR + (GALLOP_GAIN_NEAR - GALLOP_GAIN_FAR) * near, 0.2);
    // Срез растёт по экспоненте — на слух это ровное приближение.
    rampTo(chase.filter.frequency, CUTOFF_FAR * Math.pow(CUTOFF_NEAR / CUTOFF_FAR, progress), 0.2);
}

/** Ответ ушёл вовремя или раунд сменился: галоп и сердце стихают. */
export function endChaseSound() {
    stopChase(false);
}

/** Время вышло: удар, гул бронзы и храп — вместе со сценой «Попался!». */
export function caughtSound() {
    stopChase(true);
    if (!soundOn || !ctx) return;
    const now = ctx.currentTime;
    play('thud', now);
    play('gong', now + 0.03, { gain: 0.7 });
    play('snort', now + 0.35, { gain: 0.8 });
}

function startChase() {
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = CUTOFF_FAR;
    const gallop = ctx.createGain();
    gallop.gain.value = 0;
    filter.connect(gallop);
    gallop.connect(master);
    const heart = ctx.createGain();
    heart.connect(master);
    chase = {
        filter, gallop, heart,
        progress: 0, heartBpm: 0, lastTick: performance.now(), lastStride: -1,
        nextStride: ctx.currentTime + 0.05, nextBeat: 0,
        timer: setInterval(schedule, SCHEDULE_MS),
    };
    ['gallop', 'heartbeat'].forEach(decode);
}

function schedule() {
    if (!chase) return;
    if (performance.now() - chase.lastTick > STALE_MS) {
        stopChase(false);
        return;
    }
    const now = ctx.currentTime;
    const horizon = now + LOOKAHEAD;
    const gallop = buffers.get('gallop');
    if (gallop) {
        // После паузы (свёрнутая вкладка) пропущенные шаги не догоняем разом.
        if (chase.nextStride < now) chase.nextStride = now + 0.02;
        const strides = Math.max(1, Math.round(gallop.duration / STRIDE_SLOT));
        while (chase.nextStride < horizon) {
            // Случайный шаг, но не тот же подряд: без «пулемёта».
            let stride = Math.floor(Math.random() * strides);
            if (strides > 1 && stride === chase.lastStride) stride = (stride + 1) % strides;
            chase.lastStride = stride;
            play('gallop', chase.nextStride, {
                offset: stride * STRIDE_SLOT, duration: STRIDE_SLOT, out: chase.filter,
                gain: 0.85 + Math.random() * 0.15, rate: 0.97 + Math.random() * 0.06,
            });
            chase.nextStride += STRIDE_FAR + (STRIDE_NEAR - STRIDE_FAR) * chase.progress;
        }
    }
    if (chase.heartBpm && buffers.has('heartbeat')) {
        if (chase.nextBeat < now) chase.nextBeat = now + 0.02;
        while (chase.nextBeat < horizon) {
            play('heartbeat', chase.nextBeat, { gain: 0.8, out: chase.heart });
            chase.nextBeat += 60 / chase.heartBpm;
        }
    }
}

/** Погоня молчит: плавно (ответил вовремя) или сразу (догнал, звук выключен). */
function stopChase(abrupt) {
    if (!chase) return;
    const { filter, gallop, heart, timer } = chase;
    chase = null;
    clearInterval(timer);
    rampTo(gallop.gain, 0, abrupt ? 0.02 : 0.3);
    rampTo(heart.gain, 0, abrupt ? 0.02 : 0.15);
    setTimeout(() => [filter, gallop, heart].forEach(node => node.disconnect()),
        abrupt ? 300 : 2000);
}

/** AudioContext создаётся и запускается только внутри жеста игрока. */
function ensureContext() {
    if (!ctx) {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) return null;
        ctx = new Context();
        master = ctx.createGain();
        master.connect(ctx.destination);
    }
    if (ctx.state !== 'running') ctx.resume().catch(() => {});
    return ctx;
}

function load(...names) {
    names.forEach(name => {
        if (loading.has(name) || buffers.has(name)) return;
        const request = fetch(`${base}${name}.mp3?v=${version}`).then(response => {
            if (!response.ok) throw new Error(`${name}.mp3: ${response.status}`);
            return response.arrayBuffer();
        });
        loading.set(name, request);
        // Неудача не мешает игре: звук пропускается, следующий вход повторит.
        request.catch(() => loading.delete(name));
    });
}

/** Готовый AudioBuffer (null — не загрузился или не декодировался). */
function decode(name) {
    if (buffers.has(name)) return Promise.resolve(buffers.get(name));
    if (!decoding.has(name)) {
        load(name);
        const job = loading.get(name)
            // Колбэки, а не Promise: так decodeAudioData понимает и старый Safari.
            .then(data => new Promise((resolve, reject) =>
                ctx.decodeAudioData(data, resolve, reject)))
            .then(buffer => {
                buffers.set(name, buffer);
                return buffer;
            })
            .catch(() => {
                loading.delete(name);
                return null;
            })
            .finally(() => decoding.delete(name));
        decoding.set(name, job);
    }
    return decoding.get(name);
}

/** Сыграть буфер в момент when по часам AudioContext; не готов — тишина. */
function play(name, when, { gain = 1, rate = 1, offset = 0, duration, out = master } = {}) {
    const buffer = buffers.get(name);
    if (!buffer) return;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = rate;
    const level = ctx.createGain();
    level.gain.value = gain;
    source.connect(level);
    level.connect(out);
    source.onended = () => level.disconnect();
    if (duration === undefined) source.start(when, offset);
    else source.start(when, offset, duration);
}

function rampTo(param, value, timeConstant) {
    param.cancelScheduledValues(ctx.currentTime);
    param.setTargetAtTime(value, ctx.currentTime, timeConstant);
}

function syncButton(button) {
    button.setAttribute('aria-pressed', String(soundOn));
    button.title = soundOn ? 'Выключить звук' : 'Включить звук';
}

function readPreference() {
    try {
        return localStorage.getItem(STORAGE_KEY) !== 'off';
    } catch (e) {
        return true;
    }
}

function savePreference() {
    try {
        localStorage.setItem(STORAGE_KEY, soundOn ? 'on' : 'off');
    } catch (e) {
        // приватный режим: выбор живёт до перезагрузки страницы
    }
}
