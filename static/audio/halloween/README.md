# Звуки «Ночи Медного всадника» (временно)

Все записи — с [Freesound](https://freesound.org) под лицензией
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/): можно
использовать и изменять без указания авторства. Авторы перечислены из
благодарности и чтобы файл можно было пересобрать.

| Файл | Где звучит | Исходник | Кусок |
|---|---|---|---|
| `water.mp3` | волна входа в режим и выхода | [Rushing Through Canal Lock 03](https://freesound.org/people/Sonicquinn/sounds/439232/), Sonicquinn | 10,0–11,9 с; нарастает 0,6 с, стихает с 1,0 с |
| `thud.mp3` | волна входа; поимка | [impact-stone-heavy](https://freesound.org/people/kasparsj/sounds/513694/), kasparsj | 0,07–1,10 с |
| `gong.mp3` | поимка: гул бронзы | [Gong](https://freesound.org/people/ANARKYA/sounds/421114/), ANARKYA | вся запись, затухание с 1,3 с |
| `snort.mp3` | поимка: храп коня | [horse snort](https://freesound.org/people/bruno.auzet/sounds/538697/), bruno.auzet | 7,70–8,60 с |
| `heartbeat.mp3` | сердце в последние 10 с | [heartbeat-60bpm](https://freesound.org/people/loudernoises/sounds/332821/), loudernoises | 1,97–2,75 с — один «тук-тук» |
| `gallop.mp3` | галоп в раунде | [R14-38 Horse Gallops on Cobblestone](https://freesound.org/people/craigsmith/sounds/479769/), craigsmith | четыре шага: 4,18 (0,50 с), 4,91 (0,44 с), 5,87 (0,43 с), 6,31 (0,47 с) |

Обработка (ffmpeg): моно 44,1 кГц, фейды на краях кусков, громкость по
EBU R128 к −16 LUFS, но не выше −1 дБ true peak, MP3 96 кбит/с. В
`gallop.mp3` каждый шаг лежит в начале своей секунды, остаток секунды —
тишина: `halloween-sound.js` ставит шаги по одному, с офсетом `n` секунд.
