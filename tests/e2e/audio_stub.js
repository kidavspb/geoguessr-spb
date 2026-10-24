// Заглушка Web Audio для E2E: вместо звука — журнал в window.__audio.
// Файлы звуков тесты подменяют текстом с именем, «декодер» читает его.
(() => {
    const DURATIONS = { water: 1.8, thud: 1.2, gong: 2.5, snort: 1.2, gallop: 4, heartbeat: 0.8 };
    const log = window.__audio = { contexts: 0, started: [] };

    class Param {
        constructor(value) { this.value = value; }
        setValueAtTime(value) { this.value = value; return this; }
        setTargetAtTime(value) { this.value = value; return this; }
        cancelScheduledValues() { return this; }
    }

    class Node {
        connect(node) { return node; }
        disconnect() {}
    }

    class Gain extends Node {
        constructor() { super(); this.gain = new Param(1); }
    }

    class Filter extends Node {
        constructor() { super(); this.type = 'lowpass'; this.frequency = new Param(350); }
    }

    class Source extends Node {
        constructor() { super(); this.buffer = null; this.playbackRate = new Param(1); }
        start(when = 0, offset = 0, duration = null) {
            log.started.push({ name: this.buffer.name, when, offset, duration });
        }
        stop() {}
    }

    class FakeAudioContext {
        constructor() {
            log.contexts += 1;
            this.state = 'running';
            this.destination = new Node();
            this.origin = performance.now();
        }
        get currentTime() { return (performance.now() - this.origin) / 1000; }
        createGain() { return new Gain(); }
        createBiquadFilter() { return new Filter(); }
        createBufferSource() { return new Source(); }
        decodeAudioData(data, resolve, reject) {
            const name = new TextDecoder().decode(data);
            setTimeout(() => (name in DURATIONS
                ? resolve({ name, duration: DURATIONS[name] })
                : reject(new Error(`not audio: ${name}`))), 0);
        }
        resume() { this.state = 'running'; return Promise.resolve(); }
        suspend() { this.state = 'suspended'; return Promise.resolve(); }
    }

    window.AudioContext = FakeAudioContext;
    window.webkitAudioContext = FakeAudioContext;
})();
