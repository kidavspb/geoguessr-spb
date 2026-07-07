/**
 * Заглушка Яндекс.Карт для E2E-тестов: реализует ровно то подмножество
 * API v3 (карты) и v2 (панорамы), которое использует игра. Панорама
 * «находится» в любой точке, клик по карте транслируется в onClick
 * слушателей — полный игровой цикл проходит без сети и без ключей.
 */

// ---- API v3 (карта выбора / результата / финала) ----
window.ymaps3 = {
    ready: Promise.resolve(),

    YMap: class {
        constructor(container, opts) {
            this._el = container;
            this._listeners = [];
            container.classList.add('stub-map');
            container.style.background = '#dfe8d0';
            container.addEventListener('click', (e) => {
                const rect = container.getBoundingClientRect();
                const lon = 30.15 + 0.35 * (e.clientX - rect.left) / Math.max(rect.width, 1);
                const lat = 60.02 - 0.15 * (e.clientY - rect.top) / Math.max(rect.height, 1);
                this._listeners.forEach(cb => cb(null, { coordinates: [lon, lat] }));
            });
        }
        addChild(child) {
            if (child && child.__onClick) this._listeners.push(child.__onClick);
            if (child && child.__el) this._el.appendChild(child.__el);
            return child;
        }
        removeChild(child) {
            if (child && child.__el && child.__el.parentNode) child.__el.remove();
        }
        setLocation() {}
    },

    YMapDefaultSchemeLayer: class {},
    YMapDefaultFeaturesLayer: class {},

    YMapListener: class {
        constructor(props) { this.__onClick = props.onClick; }
    },

    YMapMarker: class {
        constructor(props, el) {
            this.__el = el || document.createElement('div');
            this.__el.classList.add('stub-marker');
        }
    },

    YMapFeature: class {
        constructor() {
            this.__el = document.createElement('div');
            this.__el.className = 'stub-line';
        }
    }
};

// ---- API v2 (панорамы, клиентский геокодер) ----
class StubPanorama {
    constructor(lat, lon) { this._pos = [lat, lon, 0]; }
    getPosition() { return this._pos; }
}

class StubPlayer {
    constructor(container, panorama) {
        this._pano = panorama;
        this._handlers = {};
        container.innerHTML =
            '<div class="stub-pano" style="width:100%;height:100%;background:#445"></div>';
        this.events = {
            add: (name, cb) => {
                (this._handlers[name] = this._handlers[name] || []).push(cb);
                // панорама «загружается» мгновенно
                if (name === 'panoramachange') setTimeout(cb, 30);
            }
        };
    }
    getPanorama() { return this._pano; }
    moveTo(point) {
        this._pano = new StubPanorama(point[0], point[1]);
        return Promise.resolve();
    }
    destroy() {}
}

window.ymaps = {
    ready: (cb) => { if (cb) cb(); },
    panorama: {
        // Панорама есть в любой точке — ровно там, где спросили
        locate: async (point) => [new StubPanorama(point[0], point[1])],
        Player: StubPlayer
    },
    geocode: async () => ({
        geoObjects: {
            get: () => ({ properties: { get: () => 'Тестовая улица, 1' } })
        }
    })
};
