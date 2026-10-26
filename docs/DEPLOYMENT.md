# Развёртывание

```bash
python scripts/bootstrap_platform.py
docker compose --env-file .env.docker.local up --build -d
docker compose --env-file .env.docker.local ps
docker compose --env-file .env.docker.local logs --tail 100 api worker
```

Сайт: http://localhost:8080 . Нужен Docker Compose. Bootstrap создаёт локальный файл, не перезаписывает существующий и не выводит пароли. Он генерирует пароль PostgreSQL, ключ Fernet и токен executor. API-ключ модели вводится в интерфейсе и хранится зашифрованным в PostgreSQL.

Текущие образы собираются для Linux/amd64; ARM-сборка не проверена. Host Python нужен только для короткого bootstrap-скрипта, ML/Node/PostgreSQL устанавливаются внутри контейнеров.

Compose ждёт PostgreSQL healthcheck и завершения Alembic перед API/worker. Использованы `service_healthy` и `service_completed_successfully`. [Docker Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/)

PostgreSQL содержит прикладное состояние, исследовательские факты и checkpoints. Артефакты находятся в volumes `sonder_uploads`, `sonder_runs`, БД — `sonder_postgres`. Executor получает только runs volume, без ключа модели, DB credentials и Docker socket; сеть executor закрыта для интернета.

## Модель и ресурсы

По умолчанию `ENABLE_MODEL_CALLS=false`. Для включения сначала сохранить подключение в профиле, проверить ожидающие сообщения PM и настройки проекта, затем поставить true в `.env.docker.local` и выполнить:

```bash
docker compose --env-file .env.docker.local up -d --no-deps --force-recreate api worker
```

После включения worker обрабатывает pending-сообщения PM. Исследование создаётся кнопкой «Начать». Старый конкурсный `.env` не импортируется.

`EXECUTOR_CPUS` и `EXECUTOR_MEMORY_GB` задают доступные контейнеру ресурсы; проект не может запросить больше. Один worker исполняет одно исследование одновременно, остальные ждут. Параллельность запросов ограничивается на уровне проекта, включая PM активного Run, и всего worker.

## Денежный лимит

В дополнительных настройках подключения можно указать JSON тарифа:

```json
{"currency":"RUB","input_per_million":100,"output_per_million":200,"max_input_tokens":65536,"max_output_tokens":32768}
```

Это пример формата, не реальные цены. Заменить числа подтверждёнными ценами и ёмкостями конкретного маршрута. Динамическая цена/маршрутизация без известной верхней границы не даёт гарантии бюджета. Резерв рассчитан на полный запрос с запасом; max_tokens не добавляется. Неизвестный прерванный запрос учитывается полным резервом. Без тарифа стоимость показывается по данным API либо как недоступная. Расход других приложений с тем же ключом сюда не входит. PM без активного Run учитывается отдельно в событиях проекта.

## Обновление и backup

После публикации в GHCR можно использовать готовые образы: добавить `SONDER_IMAGE_PREFIX=ghcr.io/owner/repository` и `SONDER_VERSION=v0.1.0` в локальный env, затем выполнить `docker compose --env-file .env.docker.local -f compose.yaml -f compose.release.yaml pull` и `docker compose --env-file .env.docker.local -f compose.yaml -f compose.release.yaml up --no-build -d`. Для закрытых пакетов потребуется предварительный docker login ghcr.io. Не использовать release override до фактической публикации образов.

Перед обновлением поставить исследования на паузу и сохранить резервную копию. Переключиться на проверенный тег, собрать образы. При миграциях остановить api/worker, выполнить `docker compose --env-file .env.docker.local run --rm migrate`, затем поднять сервисы.

Backup включает SQL dump, оба файловых volume и `.env.docker.local`. Без WORKSPACE_ENCRYPTION_KEY сохранённый ключ модели не расшифровать. Пример для Bash: `docker compose --env-file .env.docker.local exec -T postgres pg_dump -U sonder -d sonder -Fc > backup.dump`. В PowerShell для бинарного dump использовать подходящее перенаправление либо сохранить файл внутри контейнера и скопировать через docker cp. Восстанавливать согласованные по времени БД и артефакты в пустой экземпляр через pg_restore.

`docker compose down` сохраняет volumes. `down -v` удаляет данные и не используется для обычного обновления.

## Удалённый доступ

По умолчанию HTTP привязан к 127.0.0.1. Для личного доступа подходит SSH tunnel к 8080. Публикация через домен требует внешнего reverse proxy с HTTPS и аутентификацией. Встроенной многопользовательской авторизации нет; это одно доверенное пространство. Прямой публичный доступ без аутентификации не поддерживается. Для враждебных арендаторов нужна отдельная модель владения данными и более строгая изоляция исполнителей.

Для своего домена также добавить его в `ALLOWED_HOSTS` локального env. По умолчанию API принимает только localhost/127.0.0.1 и внутреннее имя api.
