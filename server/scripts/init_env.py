from pathlib import Path


backend = Path(__file__).resolve().parents[1]
env_path = backend / ".env"

if env_path.exists():
    print("ENV_ALREADY_EXISTS")
else:
    content = "\n".join([
        "KEJI_FFMPEG_PATH=ffmpeg",
        "KEJI_SENSEVOICE_ENABLED=false",
        "KEJI_SENSEVOICE_PYTHON=/opt/keji-sensevoice/venv/bin/python",
        "KEJI_SENSEVOICE_MODEL=/opt/keji-sensevoice/model/SenseVoiceSmall",
        "KEJI_SENSEVOICE_PUNC_MODEL=iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        "KEJI_SENSEVOICE_THREADS=8",
        "KEJI_SENSEVOICE_WORKERS=2",
        "KEJI_SENSEVOICE_CHUNK_SECONDS=30",
        "KEJI_SENSEVOICE_CHUNK_OVERLAP_SECONDS=2",
        "KEJI_OCR_MODEL=PP-OCRv6_medium",
        "KEJI_OCR_CPU_THREADS=2",
        "KEJI_OCR_ENABLE_HPI=true",
        "",
    ])
    env_path.write_text(content, encoding="utf-8")
    env_path.chmod(0o600)
    print("ENV_CREATED")
