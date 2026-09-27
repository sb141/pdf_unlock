from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    max_files: int = 20
    max_file_size_mb: int = 25
    max_total_size_mb: int = 200
    temp_dir: str = "/tmp/pdf_unlock"
    download_ttl_seconds: int = 300
    edit_session_ttl_seconds: int = 3600
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)


settings = Settings()
