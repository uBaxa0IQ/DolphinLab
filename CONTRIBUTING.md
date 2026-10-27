# Разработка

## Организация кода

Backend и executor — отдельные Python-пакеты со структурой `src`. Frontend организован по функциям продукта.
HTTP-маршруты передают запросы application-сервисам; координация исследования не зависит от HTTP.
Агентное ядро получает хранилище, планировщик заданий и монитор через явные зависимости.
PostgreSQL и удалённое выполнение реализуют инфраструктурные адаптеры.

Новая функция размещается в соответствующем модуле. Общие компоненты интерфейса находятся в `frontend/src/components`, API-клиент — в `frontend/src/lib`.
Не переносите бизнес-логику в роутеры и не добавляйте готовые ML-пайплайны в координатор: методы исследования выбирают агенты.

## Локальная среда

Основной запуск описан в README. Для разработки frontend при запущенном backend:

```bash
cd frontend
npm ci
npm run dev
```

Vite проксирует `/api` на `http://localhost:8080`. Другой адрес задаётся через `VITE_API_TARGET`.

Для установки Python-пакетов в отдельные виртуальные окружения:

```bash
pip install -e ./backend
pip install -e ./executor
```

Executor запускается в Linux-контейнере. Backend требует PostgreSQL и настроенных переменных окружения.
Миграции применяются командой `alembic -c backend/alembic.ini upgrade head`.

## Зависимости

`requirements.txt` описывает прямые зависимости, `requirements.lock` фиксирует полную среду Docker.
При обновлении зависимости меняются оба файла соответствующего сервиса.
Backend и ML-исполнитель имеют разные окружения.

Для обновления lock-файла backend можно собрать образ с `--build-arg API_REQUIREMENTS=backend/requirements.txt`, затем сохранить `python -m pip freeze` из этого образа в `backend/requirements.lock`. Для executor используются target `executor` и `EXECUTOR_REQUIREMENTS=executor/requirements.txt`.

## Стиль и сборка

Python форматируется Ruff, TypeScript и CSS — Prettier. Настройки находятся в `pyproject.toml` соответствующих пакетов и `frontend/.prettierrc.json`.

```bash
ruff check backend/src executor/src
ruff format backend/src executor/src
cd frontend
npm run format:check
npm run build
```

CI проверяет исходники, frontend, контейнерные сборки и отслеживаемые секреты. Сборка не обращается к API модели.
Изменения БД сопровождаются новой миграцией; применённые ревизии не редактируются.
Запуск исследования использует зафиксированные копии задачи, входов и настроек.

## Ветки

`feature/*`, `fix/*`, `chore/*` создаются от `dev` и возвращаются в `dev` через PR.
Для выпуска создаётся `release/X.Y.Z` → PR в `main` → тег `vX.Y.Z`.
Изменения релиза синхронизируются обратно в `dev` через `sync/*`.
Исправления опубликованной версии идут через `hotfix/*` от `main`.

Не коммитьте ключи, локальные env, базы, датасеты и результаты исследований.
