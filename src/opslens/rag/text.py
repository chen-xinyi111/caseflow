import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
MAX_CHARS = 100000


@dataclass
class Extracted:
    pages: list[str]
    format: str


def extract(filename: str, raw: bytes) -> Extracted:
    extension = Path(filename).suffix.lower()
    if extension not in (".md", ".txt", ".pdf"):
        raise ValueError("仅支持 Markdown、TXT 和文本型 PDF")
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("文件为空或超过 5 MiB")
    try:
        if extension == ".pdf":
            from pypdf import PdfReader
            if not raw.startswith(b"%PDF-"):
                raise ValueError("不是有效 PDF")
            reader = PdfReader(BytesIO(raw), strict=True)
            if reader.is_encrypted:
                raise ValueError("不支持加密 PDF")
            if len(reader.pages) > 200:
                raise ValueError("PDF 最多 200 页")
            pages = []
            length = 0
            for page in reader.pages:
                value = (page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n")
                length += len(value)
                if length > MAX_CHARS:
                    raise ValueError("提取正文超过 100000 字符")
                pages.append(value)
        else:
            pages = [raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")]
    except ValueError:
        raise
    except Exception:
        raise ValueError("文件无法解析：请使用 UTF-8 文本或有效文本型 PDF") from None
    if sum(len(page) for page in pages) > MAX_CHARS:
        raise ValueError("提取正文超过 100000 字符")
    if not any(page.strip() for page in pages):
        raise ValueError("文档没有可提取正文；扫描型 PDF 需要先提供文本版本")
    return Extracted(pages, extension[1:])


def split_pages(pages: list[str], max_chars=800, overlap=120):
    if not (0 <= overlap < max_chars):
        raise ValueError("切分重叠必须小于块大小")
    chunks = []
    for number, text in enumerate(pages, 1):
        if not text.strip():
            continue
        headings = [(match.start(), match.group(1).strip()) for match in re.finditer(r"(?m)^#{1,6}\s+(.+)$", text)]
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            if end < len(text):
                boundary = text.rfind("\n\n", start + max_chars // 2, end)
                if boundary >= 0:
                    end = boundary + 2
            heading = next((title for pos, title in reversed(headings) if pos <= start), "正文")
            if text[start:end].strip():
                chunks.append({"page": number, "start": start, "end": end,
                    "line_start": text[:start].count("\n") + 1,
                    "line_end": text[:max(start,end-1)].count("\n") + 1,
                    "heading": heading, "content": text[start:end]})
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks


def tokenize(text: str):
    result = []
    for match in re.finditer(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.casefold()):
        value = match.group()
        if re.fullmatch(r"[\u3400-\u9fff]+", value):
            result.extend(value)
            result.extend(value[i:i+2] for i in range(len(value)-1))
        else:
            result.append(value)
    return result
