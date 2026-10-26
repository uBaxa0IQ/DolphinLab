# Разработка

Схема: `feature/* → dev → release/* → main → vX.Y.Z`.

`main` — опубликованные версии, `dev` — интеграция. Ветки `feature/*`, `fix/*`, `chore/*` создаются от dev. `release/X.Y.Z` — стабилизация перед PR в main. `hotfix/*` — исправление от main с последующей синхронизацией dev через PR `sync/*`.

## Устройство

- `apps/web`: React/TypeScript и nginx.
- `apps/api/research_platform`: FastAPI, worker, LangGraph, PostgreSQL store, политики, секреты и учёт расходов.
- `apps/executor`: внутренний сервис запуска кода с отдельными ML-зависимостями.
- `research_*.py`: переиспользуемое ядро; платформенные точки расширения сохраняют конкурсный CLI.
- `migrations`: Alembic. Таблицы checkpoints мигрирует официальный PostgresSaver.

Основной запуск — Docker Compose из README. Для frontend dev-сервера: `cd apps/web`, `npm ci`, `npm run dev`. Переменная `VITE_API_TARGET=http://localhost:8080` направляет proxy к Docker; по умолчанию также используется Docker 8080.

Не коммитить ключи, `.env.docker.local`, базы, пользовательские данные и результаты. Секреты не включаются в контекст агента. Стратегию задаёт промпт, жёсткие ресурсы — политика и планировщик. Успешное выполнение кода и независимая проверка результата — разные состояния.

Docker устанавливает полные `requirements.lock`; прямые зависимости описаны в requirements.txt. Для обновления API-lock: собрать отдельный образ `docker build --target platform --build-arg API_REQUIREMENTS=apps/api/requirements.txt -t sonder-api-deps .`, затем сохранить вывод `docker run --rm --network none sonder-api-deps python -m pip freeze` в apps/api/requirements.lock. Для executor аналогично использовать target executor и EXECUTOR_REQUIREMENTS=apps/executor/requirements.txt. Оба файла коммитятся вместе.

Изменения БД сопровождаются новой постоянной миграцией; применённые ревизии не редактируются. Активный Run использует snapshot задачи, файлов и настроек. CI собирает код/контейнеры и проверяет отслеживаемые секреты, но не вызывает модель. Платные проверки выполняются отдельно с явным бюджетом.
