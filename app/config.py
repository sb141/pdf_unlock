from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    max_files: int = 20
    max_file_size_mb: int = 25
    max_total_size_mb: int = 200
    temp_dir: str = "/tmp/pdf_unlock"
    download_ttl_seconds: int = 300
    edit_session_ttl_seconds: int = 3600
    log_level: str = "INFO"
    libreoffice_path: str = ""
    conversion_timeout_seconds: int = Field(default=90, ge=1, le=600)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)


settings = Settings()
