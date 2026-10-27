from fastapi import HTTPException

from sonder.api.presenters import settings_view
from sonder.api.schemas import UpdateSettings
from sonder.infrastructure.database import WorkspaceSettings
from sonder.infrastructure.secrets import encrypt_key

from .base import Service


class SettingsService(Service):
    def get_settings(self):
        with self.sessions() as db:
            return settings_view(db.get(WorkspaceSettings, "local"))

    def update_settings(self, body: UpdateSettings):
        # The encrypted value is never returned to the browser or included in events.
        ciphertext = None
        supplied = body.api_key.get_secret_value().strip() if body.api_key else ""
        if supplied and body.clear_key:
            raise HTTPException(400, "Выбери замену или удаление ключа")
        if supplied:
            if len(supplied) > 8192:
                raise HTTPException(400, "Слишком длинный ключ")
            try:
                ciphertext = encrypt_key(supplied)
            except Exception:
                raise HTTPException(
                    500, "Не удалось зашифровать ключ. Изменения не сохранены."
                ) from None
        with self.sessions.begin() as db:
            settings = db.get(WorkspaceSettings, "local")
            if settings is None:
                settings = WorkspaceSettings(id="local")
                db.add(settings)
            settings.display_name = body.display_name
            settings.base_url = body.base_url
            settings.model = body.model.strip()
            from sonder.infrastructure.usage import Pricing

            if body.pricing:
                try:
                    settings.pricing = Pricing.model_validate(body.pricing).model_dump()
                except Exception:
                    raise HTTPException(
                        400,
                        "Для бюджета нужны валюта, цены за миллион токенов и подтверждённые ёмкости ввода/вывода",
                    ) from None
            else:
                settings.pricing = {}
            if body.clear_key:
                settings.encrypted_api_key = None
            elif ciphertext:
                settings.encrypted_api_key = ciphertext
            return settings_view(settings)
