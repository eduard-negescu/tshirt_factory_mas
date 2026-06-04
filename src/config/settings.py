from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    ollama_api_key: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    model_name: str = "llama3.2"
