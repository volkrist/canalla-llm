import os
import sys
from pathlib import Path


def application_data(settings):
    if settings.alex_llm_data_dir:
        return Path(settings.alex_llm_data_dir).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Alex LLM"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Alex LLM"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "alex-llm"


def embedding_home(settings):
    return application_data(settings) / "models/embeddings/multilingual-e5-small"
