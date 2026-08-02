/** Восстановление внешних SDK без перезагрузки страницы и потери раунда. */

export function reloadFailedScript(scriptId, errorKey, timeoutMs = 10000) {
    const previous = document.getElementById(scriptId);
    if (!previous || !previous.src) return null;

    const replacement = document.createElement('script');
    replacement.id = scriptId;
    replacement.async = true;
    replacement.src = previous.src;
    if ('fetchPriority' in replacement) {
        replacement.fetchPriority = previous.fetchPriority || 'auto';
    }
    const loaded = new Promise((resolve, reject) => {
        let settled = false;
        const finish = callback => value => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            callback(value);
        };
        const timeout = setTimeout(() => {
            window.yandexMapsLoadErrors = window.yandexMapsLoadErrors || {};
            window.yandexMapsLoadErrors[errorKey] = true;
            finish(reject)(new Error(`Таймаут повторной загрузки SDK ${errorKey}`));
        }, timeoutMs);
        replacement.onload = () => {
            if (window.yandexMapsLoadErrors) {
                delete window.yandexMapsLoadErrors[errorKey];
            }
            finish(resolve)();
        };
        replacement.onerror = () => {
            window.yandexMapsLoadErrors = window.yandexMapsLoadErrors || {};
            window.yandexMapsLoadErrors[errorKey] = true;
            finish(reject)(new Error(`SDK ${errorKey} не загрузился повторно`));
        };
    });

    window.yandexMapsLoadErrors = window.yandexMapsLoadErrors || {};
    delete window.yandexMapsLoadErrors[errorKey];
    previous.replaceWith(replacement);
    return loaded;
}
