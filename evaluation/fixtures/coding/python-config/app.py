from pathlib import Path


def load_port():
    text = Path("config.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("port"):
            return int(line.split("=")[1].strip())
    raise RuntimeError("port missing")
