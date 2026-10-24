from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="KEJI_",
        extra="ignore",
    )

    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    ffmpeg_path: str = "ffmpeg"
    sensevoice_enabled: bool = False
    sensevoice_python: Path = Path("/opt/keji-sensevoice/venv/bin/python")
    sensevoice_model: str = "/opt/keji-sensevoice/model/SenseVoiceSmall"
    sensevoice_punc_model: str = "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"
    sensevoice_threads: int = 6
    sensevoice_workers: int = 2
    sensevoice_timeout_seconds: int = 90
    sensevoice_chunk_seconds: int = 30
    sensevoice_chunk_overlap_seconds: int = 2
    ocr_model: str = "PP-OCRv6_medium"
    ocr_cpu_threads: int = 2
    ocr_enable_hpi: bool = True
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 180
    correction_llm_enabled: bool = False
    correction_llm_base_url: str = "http://127.0.0.1:8081/v1"
    correction_llm_api_key: str = "local"
    correction_llm_model: str = "qwen3-1.7b"
    correction_llm_timeout_seconds: int = 30
    update_apk_version: str = ""
    update_apk_name: str = "课迹-debug.apk"
    update_apk_notes: str = ""

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'keji.db'}"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
