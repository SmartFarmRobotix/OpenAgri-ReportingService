from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings
from password_validator import PasswordValidator
from typing import Optional, Any

from os import path, environ

PLACEHOLDER_JWT_SIGNING_KEYS = {
    "change-me",
    "some-key",
    "dev_jwt_signing_key",
    "smoketest-jwt-key-change-in-production",
}


class Settings(BaseSettings):
    PROJECT_ROOT: str = path.dirname(path.dirname(path.realpath(__file__)))

    REPORTING_GATEKEEPER_USERNAME: str
    REPORTING_GATEKEEPER_PASSWORD: str
    REPORTING_BACKEND_CORS_ORIGINS: Any

    REPORTING_POSTGRES_USER: str
    REPORTING_POSTGRES_PASSWORD: str
    REPORTING_POSTGRES_DB: str
    REPORTING_POSTGRES_HOST: str
    REPORTING_POSTGRES_PORT: int
    REPORTING_SERVICE_NAME: str
    REPORTING_SERVICE_PORT: int
    REPORTING_USING_GATEKEEPER: bool = True
    REPORTING_INFERENCE_SERVICE_NAME: str = "inference"
    REPORTING_GATEKEEPER_BASE_URL: str
    REPORTING_FARMCALENDAR_BASE_URL: str = "api/proxy/farmcalendar/api/v1"
    REPORTING_FARMCALENDAR_URLS: dict = {
        "irrigations": "/IrrigationOperations/",
        "fertilization": "/FertilizationOperations/",
        "pesticides": "/CropProtectionOperations/",
        "pest": "/Pesticides/",
        "activity_types": "/FarmCalendarActivityTypes/",
        "observations": "/Observations/",
        "operations": "/CompostOperations/",
        "turning_operations": "/CompostTurningOperations/",
        "activities": "/FarmCalendarActivities/",
        "parcel": "/FarmParcels/",
        "animals": "/FarmAnimals/",
        "materials": "/AddRawMaterialOperations/",
        "machines": "/AgriculturalMachines/",
        "farm": "/Farm/"
    }

    PDF_DIRECTORY: str = "user_reports/"

    REPORTING_EDGE_REPORT_OWNER_USER_ID: Optional[str] = None

    IMAGES_DIRECTORY: str = "session_images/"

    SQLALCHEMY_DATABASE_URI: Optional[str] = None

    @field_validator("SQLALCHEMY_DATABASE_URI", mode="before")
    def assemble_db_connection(cls, v: Optional[str], values) -> Any:
        if isinstance(v, str):
            return v

        url = "postgresql://{}:{}@{}:{}/{}".format(
            environ.get("REPORTING_POSTGRES_USER"),
            environ.get("REPORTING_POSTGRES_PASSWORD"),
            environ.get("REPORTING_POSTGRES_HOST"),
            environ.get("REPORTING_POSTGRES_PORT", 5439),
            environ.get("REPORTING_POSTGRES_DB"),
        )

        return url

    PASSWORD_SCHEMA_OBJ: PasswordValidator = PasswordValidator()
    PASSWORD_SCHEMA_OBJ.min(8).max(
        100
    ).has().uppercase().has().lowercase().has().digits().has().no().spaces()
    JWT_ACCESS_TOKEN_EXPIRATION_TIME: int
    JWT_SIGNING_KEY: str

    REPORTING_GATEKEEPER_TIMEOUT_SECONDS: float = 5

    GK_REGISTRATION_TIMEOUT_SECONDS: float = 5

    @field_validator("REPORTING_EDGE_REPORT_OWNER_USER_ID")
    def empty_edge_report_owner_is_unset(cls, v: Optional[str]) -> Optional[str]:
        return v or None

    @field_validator("JWT_SIGNING_KEY")
    def reject_placeholder_jwt_signing_key(cls, v: str) -> str:
        if v in PLACEHOLDER_JWT_SIGNING_KEYS:
            raise ValueError(
                f"JWT_SIGNING_KEY is set to a known placeholder secret ({v!r}). "
                "Set a real, unique JWT_SIGNING_KEY before starting the service."
            )
        return v


settings = Settings()
