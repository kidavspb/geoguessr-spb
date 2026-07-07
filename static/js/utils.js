/**
 * Утилиты: текст, DOM, меркаторская математика, тосты, метрика.
 */

/**
 * Склонение существительного по числу: plural(3, ['очко', 'очка', 'очков'])
 */
export function plural(n, forms) {
    const abs = Math.abs(n) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return forms[2];
    if (last === 1) return forms[0];
    if (last >= 2 && last <= 4) return forms[1];
    return forms[2];
}

/**
 * Экранирование HTML
 */
export function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Создать DOM-элемент фирменного пина для маркера на карте
 */
export function createPinElement(src) {
    const img = document.createElement('img');
    img.src = src;
    img.className = 'map-pin';
    img.alt = '';
    return img;
}

/**
 * CSS-класс строки очков в списках
 */
export function getScoreClass(score) {
    if (score >= 4000) return 'score-excellent';
    if (score >= 2500) return 'score-good';
    if (score >= 1000) return 'score-average';
    return 'score-poor';
}

/**
 * Переключение экранов
 */
export function showScreen(screenId) {
    document.querySelectorAll('.screen').forEach(screen => {
        screen.classList.remove('active');
    });
    document.getElementById(screenId).classList.add('active');
}

/**
 * Меркаторская математика для ручного вписывания точек в кадр:
 * встроенное вписывание bounds в API v3 срабатывает до того,
 * как карта узнаёт размер контейнера.
 */
export const TILE_SIZE = 256;

export function mercatorY(lat) {
    const clamped = Math.max(-85, Math.min(85, lat));
    const rad = clamped * Math.PI / 180;
    return (1 - Math.log(Math.tan(Math.PI / 4 + rad / 2)) / Math.PI) / 2;
}

export function mercatorYInv(y) {
    return (2 * Math.atan(Math.exp(Math.PI * (1 - 2 * y))) - Math.PI / 2) * 180 / Math.PI;
}

/**
 * Всплывающее уведомление внизу экрана
 */
let toastTimeout = null;

export function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.classList.remove('hidden');
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => toast.classList.add('hidden'), 3000);
}

/**
 * Цель Яндекс Метрики (no-op, если счётчик не подключён)
 */
export function reachGoal(name) {
    if (typeof window.ym === 'function' && window.METRIKA_ID) {
        window.ym(window.METRIKA_ID, 'reachGoal', name);
    }
}
