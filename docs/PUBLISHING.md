# Публикация на GitHub

Локально созданы main и dev. Платформа развивается в dev; main остаётся предыдущей точкой до релизного PR. Удалённый репозиторий этой задачей не создаётся и не изменяется.

## Первый push

Создать пустой GitHub-репозиторий без автоматически созданных файлов. Подставить свой адрес:

```bash
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
git push -u origin dev
```

Если origin существует, сначала проверить адрес, не заменять автоматически. Не делать force push. Конкурсные теги можно отправить отдельно.

Проверка файлов: `python scripts/check_tracked_secrets.py`. Это ограниченный скан отслеживаемых файлов и ZIP, не полный аудит истории. Ключи, локальные env, базы и датасеты не коммитятся. Если настоящий ключ когда-либо попадал в историю, отозвать его и очистить историю отдельно до публикации.

## Релиз

Обычные изменения: feature/*, fix/*, chore/* от dev → PR в dev. Для выпуска:

```bash
git switch dev
git pull --ff-only
git switch -c release/0.1.0
```

В релизной ветке установить VERSION=0.1.0, обновить CHANGELOG и описание проверок. Отправить ветку и создать PR release/0.1.0 → main. После merge:

```bash
git switch main
git pull --ff-only
git tag -a v0.1.0 -m "Release 0.1.0"
git push origin v0.1.0
```

Workflow проверяет принадлежность коммита main и совпадение VERSION, затем публикует `ghcr.io/OWNER/REPOSITORY/{api,worker,executor,web}:v0.1.0`. GITHUB_TOKEN получает packages:write; ключ модели не нужен. Visibility контейнерных пакетов настраивается отдельно. [GitHub: publishing Docker images](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)

После релиза создать sync/v0.1.0 от dev, merge main и PR в dev. Hotfix создаётся от main, попадает в main через hotfix/* и затем возвращается в dev.

## Защита веток

В GitHub настроить rulesets/branch protection: запрет force push и удаления main/dev, PR перед merge, успешные проверки `Source and frontend`, `Container build`, `Branch policy`. main принимает только release/* и hotfix/*. Для одиночного проекта не требовать чужого approval без второго участника.

Workflow не устанавливает серверные правила сам. Required checks добавляются после их первого появления. Доступность защиты зависит от типа репозитория и тарифа GitHub. [GitHub: protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

CI проверяет синтаксис, сборку frontend/контейнеров и отслеживаемые секреты. Автотесты и платный прогон пока не запускались по запросу пользователя. Перед заявлением о стабильности новой платформы их нужно отдельно выполнить и описать.
