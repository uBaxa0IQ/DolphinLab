# Приложения Sonder

Основной запуск, архитектура и ограничения описаны в корневом README.md.

- web: React/TypeScript, демоданные отделены от реальных проектов; dev 5179, Docker 8080.
- api: FastAPI, PostgreSQL, PM, worker, LangGraph и политика запуска.
- executor: отдельное исполнение генерируемого кода, без DB/model credentials.

Документация: docs/DEPLOYMENT.md, docs/OPERATIONS.md, docs/PUBLISHING.md и docs/VALIDATION.md (относительно корня репозитория).

Старый запуск API 8017 с SQLite больше не используется. Данные `.workspace-data` сохранены на диске, автоматически не импортируются. Vite proxy направлен на Docker 8080. Платные вызовы по умолчанию выключены; демопроекты вымышлены.
