import re


class DocumentChunker:
    """Token-aware, paragraph/sentence boundaries, overlap, preserves page/section."""

    def __init__(self, token_count, target_tokens=450, overlap_tokens=64):
        self.count = token_count
        self.target = target_tokens
        self.overlap = overlap_tokens

    def prefix(self, text, limit):
        low, high = 1, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self.count(text[:middle]) <= limit:
                low = middle
            else:
                high = middle - 1
        return low

    def chunk(self, parts, max_chunks):
        result = []
        # Combine adjacent paragraphs only within the same page and heading.
        groups = []
        for part in parts:
            if groups and all(groups[-1][key] == part[key] for key in ("page_number", "section_title")):
                groups[-1]["content"] += "\n\n" + part["content"]
            else:
                groups.append(dict(part))
        for part in groups:
            text, start = part["content"], 0
            while start < len(text):
                end = start + self.prefix(text[start:], self.target)
                if end < len(text):
                    boundaries = list(re.finditer(r"\n\n|(?<=[.!?。])\s+", text[start:end]))
                    if boundaries and boundaries[-1].end() > (end - start) // 2:
                        end = start + boundaries[-1].end()
                content = text[start:end].strip()
                if content:
                    result.append({**part, "content": content, "token_count": self.count(content)})
                    if len(result) > max_chunks:
                        raise ValueError("Document chunk limit exceeded")
                if end >= len(text):
                    break
                tail = text[start:end]
                # Count a bounded trailing overlap without silently truncating the embedding input.
                overlap = self.prefix(tail[::-1], self.overlap)
                start = max(start + 1, end - overlap)
        return result
