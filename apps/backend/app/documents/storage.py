import re
from abc import ABC, abstractmethod
from pathlib import Path


class DocumentStorage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes): ...

    @abstractmethod
    def path(self, key: str) -> Path: ...

    @abstractmethod
    def delete(self, key: str): ...


class LocalDocumentStorage(DocumentStorage):
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, key):
        if not re.fullmatch(r"[a-f0-9]{32}", key):
            raise ValueError("Invalid storage key")
        target = (self.root / key).resolve()
        if target.parent != self.root:
            raise ValueError("Invalid storage path")
        return target

    def put(self, key, data):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path(key).open("xb") as file:
            file.write(data)

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)
