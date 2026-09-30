# Границы административных районов Санкт-Петербурга

`spb_districts.geojson` — canonical source of truth для карты выбора района и
серверных spatial checks. Это GeoJSON `FeatureCollection` в WGS 84
(долгота/широта, RFC 7946), содержащий ровно 18 административных районов
Санкт-Петербурга. Внутригородские муниципальные образования (`admin_level=8`)
сюда не входят.

## Источник и snapshot

Геометрия получена из [OpenStreetMap](https://www.openstreetmap.org/copyright)
2026-09-01. Выбраны relations с `boundary=administrative`, `admin_level=5` и
`type=boundary`, находящиеся внутри relation Санкт-Петербурга
[`337422`](https://www.openstreetmap.org/relation/337422). Контрольный Overpass
query на эту дату возвращал ровно 18 объектов:

```overpass
[out:json][timeout:120];
rel(337422);
map_to_area->.spb;
rel(area.spb)["boundary"="administrative"]["admin_level"="5"];
out meta;
```

| ID приложения | Район | OSM relation | Версия relation | Изменён в OSM |
|---|---|---:|---:|---|
| `admiralteysky` | Адмиралтейский | [1114193](https://www.openstreetmap.org/relation/1114193) | 102 | 2025-12-01 |
| `vasileostrovsky` | Василеостровский | [1114252](https://www.openstreetmap.org/relation/1114252) | 44 | 2023-09-16 |
| `vyborgsky` | Выборгский | [1114354](https://www.openstreetmap.org/relation/1114354) | 179 | 2026-08-07 |
| `kalininsky` | Калининский | [1114806](https://www.openstreetmap.org/relation/1114806) | 132 | 2026-08-07 |
| `kirovsky` | Кировский | [1114809](https://www.openstreetmap.org/relation/1114809) | 79 | 2023-10-05 |
| `kolpinsky` | Колпинский | [337424](https://www.openstreetmap.org/relation/337424) | 46 | 2023-09-16 |
| `krasnogvardeysky` | Красногвардейский | [1114895](https://www.openstreetmap.org/relation/1114895) | 64 | 2026-08-28 |
| `krasnoselsky` | Красносельский | [363103](https://www.openstreetmap.org/relation/363103) | 157 | 2026-05-02 |
| `kronshtadtsky` | Кронштадтский | [1115082](https://www.openstreetmap.org/relation/1115082) | 65 | 2026-08-20 |
| `kurortny` | Курортный | [1115366](https://www.openstreetmap.org/relation/1115366) | 114 | 2026-02-10 |
| `moskovsky` | Московский | [338636](https://www.openstreetmap.org/relation/338636) | 87 | 2023-09-16 |
| `nevsky` | Невский | [368287](https://www.openstreetmap.org/relation/368287) | 54 | 2025-12-17 |
| `petrogradsky` | Петроградский | [1114905](https://www.openstreetmap.org/relation/1114905) | 40 | 2023-09-16 |
| `petrodvortsovy` | Петродворцовый | [367375](https://www.openstreetmap.org/relation/367375) | 108 | 2026-01-06 |
| `primorsky` | Приморский | [1115367](https://www.openstreetmap.org/relation/1115367) | 115 | 2026-03-12 |
| `pushkinsky` | Пушкинский | [338635](https://www.openstreetmap.org/relation/338635) | 83 | 2026-01-26 |
| `frunzensky` | Фрунзенский | [369514](https://www.openstreetmap.org/relation/369514) | 37 | 2023-09-16 |
| `tsentralny` | Центральный | [1114902](https://www.openstreetmap.org/relation/1114902) | 70 | 2025-12-17 |

OSM relations и принятый в Петербурге `admin_level=5` также перечислены на
[странице проекта OSM «Санкт-Петербург/Районы»](https://wiki.openstreetmap.org/wiki/RU:Санкт-Петербург/Районы).
Версии и даты в таблице отдельно сверены через основной OSM API в день
получения geometry.

Характеристики сохранённого snapshot:

- 333 743 байта без сжатия, около 100 КБ при gzip;
- 15 186 вершин после упрощения (17 119 до него);
- общий envelope: 29.425758–30.759493° E, 59.633783–60.244837° N;
- 15 Polygon и 3 MultiPolygon: Красносельский (2 части и 1 hole),
  Кронштадтский (15 островных частей), Петродворцовый (2 части);
- SHA-256:
  `838e221d2ea18d7ab0ee2892b11dcc480a2493b12b96b373416a32f7381aa275`.

Для режима «Весь город» приложение лениво собирает производный
`MultiPolygon` через `coverage_union_all` всех 18 features и кэширует его на
срок жизни worker-процесса. Отдельный city polygon не хранится: source of truth
остаётся этот районный snapshot, а union не пересчитывается на каждый запрос.

## Лицензия и attribution

Данные: © участники OpenStreetMap, лицензия
[Open Data Commons Open Database License 1.0 (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).
Нормализованный и упрощённый GeoJSON является производным набором OSM и
распространяется на условиях ODbL 1.0. Это не меняет лицензию исходного кода
приложения.

При публичном показе карты нужно оставить видимую подпись
`© OpenStreetMap contributors`, ведущую на
<https://www.openstreetmap.org/copyright>. Для интерактивной карты OSMF
рекомендует размещать attribution в углу карты или непосредственно рядом с
ней: <https://osmfoundation.org/wiki/Licence/Attribution_Guidelines>.

## Воспроизводимое обновление

Генератор делает один lookup по allow-list из 18 relation ID, запрашивает
несжатую geometry из Nominatim, проверяет имена/ID/типы, собирает Polygon и
MultiPolygon, упрощает всё покрытие совместно и повторно проверяет топологию и
контрольные точки.

Это одноразовый maintenance-запрос, не runtime/CI-зависимость. Не запускайте
обновление по расписанию и соблюдайте
[Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/);
готовый результат хранится в репозитории и обслуживается локально.

```bash
python -m pip install "Shapely==2.1.1"
python tools/update_spb_districts.py
```

Параметры canonical snapshot:

- topology-preserving `coverage_simplify`, tolerance `0.00002°` (не более
  примерно 2,2 м по широте Петербурга);
- координаты округлены до 6 знаков после запятой (около 0,1 м);
- свойства каждого feature ограничены `id`, `name`, `osm_relation_id`;
- порядок features фиксирован allow-list генератора;
- Polygon/MultiPolygon, острова и holes сохраняются;
- точки на границе считаются принадлежащими области (`covers`, а не
  строгий `contains`). Если точка из-за ошибки не покрыта ни одним районом,
  вызывающий код должен явно обработать это как ошибку данных, а не выбирать
  район по умолчанию.

После обновления нужно просмотреть diff, обновить дату/версии в этом файле,
запустить geometry tests и визуально сравнить центральные районы, Курортный,
Кронштадтский, Пушкинский и Колпинский районы с OSM/РГИС.

Официальная РГИС Санкт-Петербурга публикует слой `rgis:ADM`
«Административные районы» через WMS, однако на дату snapshot публичная WFS или
GeoJSON-выгрузка отсутствовала (WFS отвечал 403/404), а capabilities не
содержал лицензии для републикации. Поэтому он использован только для ручной
сверки, а не как источник распространяемого набора. Каталог:
[WMS GetCapabilities](https://gs2.rgis.spb.ru/geoserver/wms?service=WMS&request=GetCapabilities).
