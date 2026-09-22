"""Parser subprocess boundary: time bounded, no network, bounded output."""

import json
import re
import subprocess
import sys
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path


class ExtractionError(Exception):
    pass


class DocumentExtractor(ABC):
    @abstractmethod
    def extract(self, path: Path, extension: str, max_chars: int) -> list[dict]: ...


class LocalExtractor(DocumentExtractor):
    def extract(self, path, extension, max_chars):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "app.documents.extract", str(path), extension, str(max_chars)],
                capture_output=True,
                timeout=45,
                check=False,
            )
            if result.returncode != 0:
                raise ExtractionError(
                    "Не удалось извлечь текст. Проверьте формат, размер и наличие текста; OCR не поддерживается."
                )
            return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, ValueError) as error:
            raise ExtractionError("Извлечение текста превысило лимит ресурсов.") from error


def extract_local(path, extension, max_chars):
    parts, size = [], 0

    def append(text, page=None, section=None):
        nonlocal size
        text = re.sub(r"[^\S\n]+", " ", text.replace("\x00", "")).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        size += len(text)
        if size > max_chars:
            raise ExtractionError("Text limit")
        if text:
            parts.append({"content": text, "page_number": page, "section_title": section})

    if extension in {"txt", "md"}:
        text = Path(path).read_bytes().decode("utf-8-sig", errors="strict")
        section = None
        for paragraph in re.split(r"\n\s*\n", text):
            if extension == "md" and re.match(r"^#{1,6} ", paragraph):
                section = paragraph.splitlines()[0].lstrip("# ")[:255]
            append(paragraph, section=section)
    elif extension == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) > 500:
            raise ExtractionError("Unsupported PDF")
        for index, page in enumerate(reader.pages):
            append(page.extract_text(), index + 1)
    elif extension == "docx":
        from docx import Document

        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(x.file_size for x in entries) > 50 * 1024 * 1024:
                raise ExtractionError("Archive limit")
            if any(
                x.file_size > 10 * 1024 * 1024 or x.file_size / max(x.compress_size, 1) > 200 for x in entries
            ):
                raise ExtractionError("Archive ratio limit")
            if "word/document.xml" not in archive.namelist():
                raise ExtractionError("Not DOCX")
        document = Document(path)
        section = None
        for paragraph in document.paragraphs:
            if paragraph.style and (paragraph.style.name or "").startswith("Heading"):
                section = paragraph.text[:255]
            append(paragraph.text, section=section)
        for table in document.tables:
            for row in table.rows:
                append(" | ".join(cell.text for cell in row.cells))
    else:
        raise ExtractionError("Unsupported format")
    if not parts:
        raise ExtractionError("No extractable text")
    return parts


if __name__ == "__main__":
    try:
        from .parser_limits import limit_parser_memory

        limit_parser_memory()
        sys.stdout.buffer.write(
            json.dumps(extract_local(sys.argv[1], sys.argv[2], int(sys.argv[3])), ensure_ascii=False).encode(
                "utf-8"
            )
        )
    except Exception:
        sys.exit(1)
