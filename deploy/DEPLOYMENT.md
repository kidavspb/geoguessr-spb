# Dev → GitHub → production

## 1. Целевая схема

Проект использует два окружения и две постоянные Git-ветки:

- `dev.geoguessr.spb.ru` — DEV-сервер для разработки и проверки изменений;
- `geoguessr.spb.ru` — PROD-сервер;
- `dev` — текущее состояние разработки;
- `main` — версия, допущенная к production.

Поток изменений:

```text
feature/*, fix/*, infra/*
          │
          ▼
     local dev
          │
   проверка на DEV
          │
          ▼
     origin/dev
          │
     PR dev → main
          │
       GitHub CI
          │
          ▼
     origin/main
          │
          │ PROD сам периодически проверяет main
          ▼
        PROD
```

GitHub Actions отвечает только за CI. Deployment выполняется с PROD по pull-модели:
PROD сам читает публичный `origin/main` по HTTPS и устанавливает новую версию.

Доступа с DEV-сервера или GitHub Actions к PROD нет и для этой схемы он не требуется.

---

## 2. Окружения

### DEV

Адрес:

```text
https://dev.geoguessr.spb.ru
```

DEV закрыт HTTP Basic Auth.

Пользователь:

```text
dev
```

Текущий пароль хранится только в локальном `.env` и выводится командой:

```bash
venv/bin/dotenv get DEV_HTTP_PASSWORD
```

На DEV разрешены:

- редактирование кода;
- Git-коммиты и push;
- работа с временными ветками;
- установка dev-зависимостей;
- миграции DEV-базы;
- перезапуск `geoguessr.service`;
- запуск тестов;
- ручная и автоматизированная проверка `dev.geoguessr.spb.ru`.

### PROD

PROD всегда должен работать из `main`.

На PROD не ведётся разработка и не должно быть локальных изменений tracked-файлов.

Автоматический deployment обновляет только приложение:

1. читает `origin/main`;
2. обновляет рабочую копию;
3. обновляет Python-зависимости;
4. применяет Alembic-миграции;
5. перезапускает `geoguessr.service`;
6. выполняет healthcheck.

Apache, systemd unit-файлы, deploy-скрипт, `.env`, firewall, SSH, пакеты ОС и другие настройки
PROD автоматически из Git не применяются. Такие изменения администратор выполняет вручную.

---

## 3. Что не хранится и не синхронизируется через Git

Между DEV и PROD не синхронизируются:

- `.env`;
- SQLite-база;
- `venv`;
- секреты;
- другие локальные файлы, исключённые через `.gitignore`.

Каждое окружение имеет собственную конфигурацию и собственную БД.

---

## 4. Git-модель после завершения перехода

Постоянные ветки:

```text
dev   — разработка и DEV
main  — production
```

`main` является единственным источником версии для PROD.

Для отдельных задач при необходимости используются временные ветки:

```text
feature/*
fix/*
infra/*
```

Обычно они создаются от `dev` и после завершения вливаются обратно в `dev`.

Для небольших изменений при работе одного разработчика допустимо коммитить непосредственно в `dev`.

### Merge dev → main

Релиз выполняется Pull Request'ом:

```text
dev → main
```

Для этого PR предпочтителен обычный **merge commit**, а не squash/rebase merge.

После merge `origin/main` становится потомком `dev`, поэтому DEV можно синхронизировать простым fast-forward:

```bash
git switch dev
git fetch origin
git merge --ff-only origin/main
git push origin dev
```

После этого `dev` снова содержит production-состояние и готов к следующему циклу разработки.

---

## 5. Текущий переход: `infra/dev-and-deploy`

Сейчас инфраструктурные изменения находятся в рабочей ветке:

```text
infra/dev-and-deploy
```

Эту ветку не нужно вливать напрямую в `main`.

Нужно впервые пройти будущий рабочий процесс:

```text
infra/dev-and-deploy
        │
        ▼
   local dev
        │
   тестирование
        │
        ▼
   origin/dev
        │
   PR dev → main
        │
        ▼
      main
```

### Порядок действий

Сначала закончить и проверить текущие изменения в `infra/dev-and-deploy`.

```bash
cd /root/geoguessr-spb
git switch infra/dev-and-deploy
git status
git diff
```

После проверки закоммитить изменения:

```bash
git add <нужные-файлы>
git commit -m "Настройка dev-окружения и pull-based deploy"
```

Рабочее дерево после commit должно быть чистым.

Получить актуальный `main` и создать локальную постоянную ветку `dev`:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c dev
```

Влить инфраструктурную ветку в локальный `dev`:

```bash
git merge --no-ff infra/dev-and-deploy
```

После merge **сначала проверить всё локально на DEV, не публикуя `dev` в GitHub**.

Минимальные проверки:

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/flask db upgrade
systemctl restart geoguessr.service
venv/bin/pytest -q
```

Дополнительно проверить работу:

```text
https://dev.geoguessr.spb.ru
```

и убедиться, что `geoguessr.service` стабильно работает.

Только после успешной проверки опубликовать постоянную ветку:

```bash
git push -u origin dev
```

Затем создать PR:

```text
dev → main
```

После зелёного CI выполнить merge commit.

После merge синхронизировать DEV:

```bash
git switch dev
git fetch origin
git merge --ff-only origin/main
git push origin dev
```

Временная ветка после этого больше не нужна:

```bash
git branch -d infra/dev-and-deploy
```

Если она была опубликована в GitHub:

```bash
git push origin --delete infra/dev-and-deploy
```

---

## 6. Обычная работа после перехода

Перед любой новой задачей сначала синхронизировать обе remote-ссылки и
разрешить только fast-forward. Рабочее дерево перед этим должно быть чистым:

```bash
cd /root/geoguessr-spb
git switch dev
git status --short
git fetch origin
git pull --ff-only origin dev
git merge --ff-only origin/main
```

Последняя команда будет no-op, если `main` уже содержится в `dev`. Если после
предыдущего PR в `main` появился merge-коммит, локальный `dev` fast-forward'ится
до него до начала новой работы. Если fast-forward невозможен, не начинать
редактирование и не делать rebase/force-push автоматически: сначала исследовать
расхождение веток.

Для небольшой задачи после preflight:

```bash
# разработка

venv/bin/flask db upgrade
systemctl restart geoguessr.service
venv/bin/pytest -q

git status
git diff
git add <нужные-файлы>
git commit -m "Описание изменения"
git push
```

После ручной проверки `https://dev.geoguessr.spb.ru` создать PR `dev → main`.

Для крупной или экспериментальной задачи:

```bash
git switch dev
git fetch origin
git pull --ff-only origin dev
git merge --ff-only origin/main
git switch -c feature/короткое-название

# разработка и commits

git switch dev
git merge --no-ff feature/короткое-название
```

После merge в `dev` выполнить обычные DEV-проверки и push `dev`.

---

## 7. CI

`.github/workflows/ci.yml` отвечает только за проверки кода.

Целевая схема запуска:

```yaml
on:
  push:
    branches:
      - dev
  pull_request:
    branches:
      - main
```

Основная обязательная проверка:

```text
tests
```

Она должна запускать существующий набор unit/E2E-тестов проекта.

Для workflow достаточно минимальных permissions, например:

```yaml
permissions:
  contents: read
```

В CI не должно быть production deployment logic или production credentials.

---

## 8. Защита `main`

Для `main` необходимо настроить GitHub ruleset/branch protection:

- изменения только через Pull Request;
- обязательный status check `tests`;
- merge запрещён при падающем CI;
- force-push запрещён;
- удаление ветки запрещено.

Желательно требовать актуальную ветку относительно `main` перед merge.

PROD считает любой commit, попавший в защищённый `main`, разрешённым к deployment.

---

## 9. Pull-based deployment на PROD

Deployment запускается локальным `systemd timer` на PROD:

```text
geoguessr-deploy.timer
        │
        ▼
geoguessr-deploy.service
        │
        ▼
/usr/local/sbin/geoguessr-deploy
        │
        ▼
origin/main
```

Timer впервые запускает проверку через минуту после boot, затем — примерно
через две минуты после завершения предыдущей проверки (с небольшим jitter).

Репозиторий публичный, поэтому PROD может читать его по HTTPS без GitHub credentials.

Целевой `origin` на PROD:

```text
https://github.com/kidavspb/geoguessr-spb.git
```

---

## 10. Алгоритм deploy-скрипта

Deploy должен быть консервативным: при неожиданном состоянии остановиться и записать ошибку,
а не пытаться автоматически исправлять неизвестное состояние.

Алгоритм:

```text
1. Взять lock через flock.
2. Проверить наличие repository, ветку main и ожидаемый публичный HTTPS origin.
3. Проверить отсутствие локальных изменений tracked-файлов.
4. Выполнить git fetch --prune origin main.
5. Получить SHA origin/main.
6. Прочитать SHA последнего успешного deployment и текущий HEAD.
7. Если origin/main совпадает с marker, потребовать также HEAD == marker и завершиться без изменений.
8. Проверить непрерывную fast-forward-цепочку marker → HEAD → origin/main.
9. Обновить main через fast-forward.
10. Установить requirements.txt.
11. Выполнить flask db upgrade.
12. Перезапустить geoguessr.service.
13. Выполнить healthcheck с несколькими попытками.
14. Только после успешного healthcheck записать новый deployed SHA.
```

### Lock

Одновременно должен выполняться только один deployment.

Например:

```text
/run/lock/geoguessr-deploy.lock
```

### Чистое рабочее дерево

Перед deployment tracked-файлы должны быть неизменёнными.

При грязном working tree deploy прекращается.

Deploy-скрипт не должен автоматически выполнять `git reset --hard` для исправления неизвестного состояния.

Нормальное обновление:

```bash
git fetch --prune origin main
git merge --ff-only origin/main
```

Если fast-forward невозможен, deploy завершается ошибкой и требует ручного вмешательства.

---

## 11. Marker последнего успешного deployment

Нельзя определять успешность только по текущему Git `HEAD`.

Например, Git мог уже обновиться, после чего `pip install`, migration, restart или healthcheck завершились ошибкой.
В этом случае `HEAD == origin/main`, но новая версия фактически не была успешно deployed.

Поэтому отдельно хранится SHA последнего успешного deployment:

```text
/var/lib/geoguessr-deploy/deployed-sha
```

Новая версия определяется сравнением:

```text
origin/main SHA != deployed-sha
```

Marker обновляется **только после успешного healthcheck**.

Если deployment завершился ошибкой, marker остаётся прежним и следующий запуск timer повторяет попытку.
При этом уже обновившийся `HEAD` допустим только между marker и свежим
`origin/main`: это позволяет безопасно повторить не завершившиеся шаги и не
маскирует ручной checkout, откат или переписанную историю.

---

## 12. Dependencies, migrations и restart

Dependencies:

```bash
venv/bin/pip install -r requirements.txt
```

На PROD не устанавливается `requirements-dev.txt`.

Миграции:

```bash
venv/bin/flask db upgrade
```

`AUTO_MIGRATE=false` можно оставить: миграция выполняется явным шагом deployment.

После успешных подготовительных шагов:

```bash
systemctl restart geoguessr.service
```

---

## 13. Healthcheck

После restart выполнить несколько попыток локального запроса:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/
```

Нужно учитывать короткое время запуска Gunicorn: между попытками должна быть небольшая задержка.

Только после успешного ответа deployment считается завершённым и обновляет `deployed-sha`.

---

## 14. Ошибка deployment и rollback

Автоматический rollback не выполнять.

Причина — database migrations: после `flask db upgrade` старый код не обязательно совместим с новой схемой БД.

При ошибке:

- `deployed-sha` не обновляется;
- ошибка записывается в journal;
- автоматический откат кода или БД не выполняется.

Восстановление выполняет администратор вручную.

Предпочтительный способ исправления — исправить или revert'нуть проблемное изменение в Git,
прогнать его через `dev → PR → main`, после чего PROD установит исправленную версию обычным способом.

Если требуется ручное вмешательство на PROD, перед ним можно остановить timer:

```bash
systemctl stop geoguessr-deploy.timer
```

`flask db downgrade` автоматически не выполнять.

---

## 15. Логи deployment

Deployment пишет короткий понятный лог в journald.

Просмотр:

```bash
journalctl -u geoguessr-deploy.service
```

Последние записи:

```bash
journalctl -u geoguessr-deploy.service -n 100
```

Timer:

```bash
systemctl status geoguessr-deploy.timer
systemctl list-timers geoguessr-deploy.timer
```

В логе должны быть как минимум:

- текущий `deployed-sha`;
- найденный `origin/main` SHA;
- начало deployment;
- этап ошибки;
- успешное завершение.

Секреты и содержимое `.env` в лог не выводить.

---

## 16. Deployment-файлы в repository

В Git хранится эталонная конфигурация:

```text
deploy/
├── DEPLOYMENT.md
├── apache/
│   ├── geoguessr.conf
│   └── geoguessr-dev.conf
└── systemd/
    ├── geoguessr-deploy.sh
    ├── geoguessr-deploy.service
    └── geoguessr-deploy.timer
```

Файлы `deploy/systemd/*` являются source-of-truth/templates.

Изменение этих файлов в Git **не должно автоматически менять systemd или deploy-скрипт на PROD**.

Новая версия такой инфраструктурной конфигурации сначала проходит обычный Git-flow,
после чего администратор вручную применяет её на PROD.

---

## 17. Одноразовая подготовка PROD

> Этот раздел выполняется человеком непосредственно на PROD.
> Агент на DEV только готовит корректные файлы и команды.

### 17.1. Проверить repository

На PROD:

```bash
cd /root/geoguessr-spb
git status
git branch --show-current
git remote -v
```

Ожидаемая ветка:

```text
main
```

Tracked working tree должен быть чистым.

Неожиданные изменения сначала нужно исследовать; автоматически затирать их нельзя.

### 17.2. Переключить remote на публичный HTTPS

```bash
git remote set-url origin https://github.com/kidavspb/geoguessr-spb.git
git fetch origin main
git remote -v
```

После проверки HTTPS старый GitHub deploy key можно удалить, если он больше нигде не используется.

### 17.3. Один раз вручную обновить приложение до нового main

После merge PR шаблоны deployment ещё отсутствуют в старой рабочей копии
PROD. Поэтому первый fast-forward выполняется вручную и теми же безопасными
шагами, которые затем будет выполнять timer:

```bash
cd /root/geoguessr-spb
git merge-base --is-ancestor HEAD origin/main
git merge --ff-only origin/main
venv/bin/pip install --disable-pip-version-check -r requirements.txt
venv/bin/flask --app app.py db upgrade
systemctl restart geoguessr.service
```

Дождаться готовности приложения и проверить:

```bash
for attempt in {1..20}; do
  curl --fail --silent --show-error --max-time 5 \
    --output /dev/null http://127.0.0.1:8000/ && break
  sleep 1
done
curl --fail --silent --show-error --max-time 5 \
  --output /dev/null http://127.0.0.1:8000/
```

Если `git merge-base`, установка зависимостей, migration, restart или
healthcheck завершились ошибкой, не продолжать установку timer до выяснения
причины.

### 17.4. Установить deploy-скрипт

Скопировать проверенный template из уже обновлённой рабочей копии:

```bash
cd /root/geoguessr-spb
install -m 0755 deploy/systemd/geoguessr-deploy.sh \
  /usr/local/sbin/geoguessr-deploy
```

### 17.5. Создать state directory и marker

После подтверждения, что обновлённая версия PROD исправно работает:

```bash
install -d -m 0755 /var/lib/geoguessr-deploy
cd /root/geoguessr-spb
git rev-parse HEAD > /var/lib/geoguessr-deploy/deployed-sha
chmod 0644 /var/lib/geoguessr-deploy/deployed-sha
```

### 17.6. Установить systemd units

Установить repository templates без изменений:

```bash
cd /root/geoguessr-spb
install -m 0644 deploy/systemd/geoguessr-deploy.service \
  /etc/systemd/system/geoguessr-deploy.service
install -m 0644 deploy/systemd/geoguessr-deploy.timer \
  /etc/systemd/system/geoguessr-deploy.timer
```

Затем:

```bash
systemctl daemon-reload
systemctl start geoguessr-deploy.service
systemctl status geoguessr-deploy.service
journalctl -u geoguessr-deploy.service -n 100
```

Первый запуск должен вывести текущий `deployed-sha`, тот же SHA для
`origin/main` и сообщение `no new version`.

### 17.7. Включить timer

Только после успешного ручного запуска:

```bash
systemctl enable --now geoguessr-deploy.timer
systemctl list-timers geoguessr-deploy.timer
```

---

## 18. Обязанности агента на DEV

Агент должен самостоятельно выполнить всю работу, доступную на DEV, включая:

- исследование текущего состояния `infra/dev-and-deploy`;
- приведение `.github/workflows/ci.yml` к целевой CI-схеме;
- создание/исправление `deploy/systemd/*`;
- приведение Apache examples к фактическим DEV/PROD именам;
- удаление устаревших staging-файлов, если они больше не нужны;
- обновление README и этого документа;
- проверку shell-скрипта через `bash -n`;
- запуск тестов;
- работу с локальными Git-ветками;
- создание локального `dev`;
- merge `infra/dev-and-deploy → dev`;
- проверку результата на DEV;
- push `dev` только после успешных проверок;
- подготовку PR `dev → main`.

На PROD агент ничего не выполняет.

Если для завершения работы требуется действие на PROD, агент должен:

1. точно описать, зачем оно нужно;
2. дать готовую команду или последовательность команд;
3. указать ожидаемый результат;
4. остановиться на этом месте для соответствующего PROD-шагa.

---

## 19. Что агент должен получить в итоге

Перед завершением текущей работы агент должен убедиться, что:

1. текущая `infra/dev-and-deploy` закончена и закоммичена;
2. создан локальный `dev` от актуального `main`;
3. `infra/dev-and-deploy` влита в локальный `dev`;
4. DEV-зависимости установлены;
5. DEV-миграции применены;
6. `geoguessr.service` работает;
7. `dev.geoguessr.spb.ru` проходит smoke-check;
8. `venv/bin/pytest -q` проходит;
9. `deploy/systemd/geoguessr-deploy.sh` проходит `bash -n`;
10. CI не содержит production deployment;
11. подготовлены корректные deploy script/service/timer templates;
12. устаревшая staging-конфигурация удалена или обоснованно сохранена;
13. документация соответствует фактической схеме;
14. итоговый diff просмотрен;
15. `dev` отправлена в `origin/dev`;
16. готов PR `dev → main`;
17. никаких действий на PROD не выполнено.

После merge PR администратор вручную выполняет одноразовые шаги из раздела 17 на PROD.
