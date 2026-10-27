from dolphinlab.infrastructure.database import Session, WorkspaceSettings
from dolphinlab.infrastructure.secrets import decrypt_key


def model_settings(connection=None):
    with Session() as db:
        settings = db.get(WorkspaceSettings, "local")
        if (
            not settings
            or not settings.encrypted_api_key
            or not settings.base_url
            or not settings.model
        ):
            raise ValueError("Configure model, Base URL and API key in Settings")
        if connection and any(
            connection.get(key) != getattr(settings, key) for key in ("base_url", "model")
        ):
            raise ValueError(
                "Model/provider changed since run creation; restore its connection or create a new run"
            )
        result = {
            "base_url": settings.base_url,
            "model": settings.model,
            "api_key": decrypt_key(settings.encrypted_api_key),
        }
        # A run keeps its admitted tariff and ceilings even if Settings is edited later.
        pricing = (connection or {}).get("pricing", settings.pricing) or {}
        if pricing.get("enforce_request_limits"):
            from dolphinlab.infrastructure.usage import Pricing

            limits = Pricing.model_validate(pricing)
            result.update(
                request_input_ceiling=limits.max_input_tokens,
                request_output_ceiling=limits.max_output_tokens,
            )
        return result
