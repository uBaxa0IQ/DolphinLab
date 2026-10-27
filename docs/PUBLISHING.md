# Публикация и релизы

## Первый push

Создайте пустой репозиторий на GitHub. В корне проекта выполните:

```bash
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
git push -u origin dev
```

Если `origin` уже настроен, используйте существующий адрес. В GitHub выберите `main` основной веткой.
Публикуйте нужные ветки явно. Локальные env, пользовательские данные и результаты исключены из репозитория.

## Workflow

`feature/* → dev → release/* → main → vX.Y.Z`

1. Создайте feature/fix/chore-ветку от `dev` и откройте PR в `dev`.
2. Создайте `release/X.Y.Z` от `dev`, обновите `VERSION`, версии пакетов и `CHANGELOG.md`.
3. Откройте PR релизной ветки в `main`.
4. После merge поставьте тег на релизный коммит:

```bash
git switch main
git pull --ff-only
git tag -a v0.1.0 -m "Release 0.1.0"
git push origin v0.1.0
```

5. Верните изменения `main` в `dev` через ветку `sync/X.Y.Z` и PR.

`hotfix/*` создаётся от `main`, проходит PR в `main` и синхронизируется в `dev`.

## CI и образы

CI проверяет синтаксис Python, секреты в отслеживаемых файлах, сборку frontend и Docker-сервисов.
Branch policy проверяет направление PR: `main` принимает `release/*` и `hotfix/*`.

Релизный workflow проверяет принадлежность тега истории `main` и совпадение с `VERSION`, затем публикует четыре образа:
`ghcr.io/owner/repository/{api,worker,executor,web}:vX.Y.Z`.
Публикация использует `GITHUB_TOKEN` с `packages:write`; ключ модели для неё не нужен.
Видимость контейнерных пакетов настраивается в GitHub Packages.

CI также поддерживает ручной запуск через Actions. Для повторной публикации образов workflow
`Release images` запускается на существующем релизном теге `vX.Y.Z`; проверка версии и принадлежности
истории `main` остаётся обязательной. Запуск на обычной ветке не проходит проверку релизного тега.

## Защита веток

В GitHub Rulesets для `main` и `dev` включите PR перед merge, запрет удаления и force push.
Добавьте required checks `Source and frontend`, `Container build`, `Branch policy` после первого выполнения CI.
Для персонального репозитория число обязательных approvals выбирается владельцем.
Настройки защиты применяются в GitHub отдельно от файлов workflow.
