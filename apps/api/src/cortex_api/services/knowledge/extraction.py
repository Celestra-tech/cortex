"""Upload -> text. Format detection, per-format extraction, and normalization.

Every extractor returns normalized text plus the offset at which each page
starts, so chunks can cite page numbers. Extraction is CPU-bound and
synchronous; callers run it in a worker thread.
"""

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath
from xml.etree import ElementTree

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as safe_xml
from pypdf import PdfReader
from pypdf.errors import PyPdfError


class DocumentFormat(StrEnum):
    PDF = "application/pdf"
    MARKDOWN = "text/markdown"
    TEXT = "text/plain"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


_EXTENSIONS = {
    ".pdf": DocumentFormat.PDF,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".txt": DocumentFormat.TEXT,
    ".text": DocumentFormat.TEXT,
    ".docx": DocumentFormat.DOCX,
}
_MIME_ALIASES = {
    "text/x-markdown": DocumentFormat.MARKDOWN,
    "application/x-pdf": DocumentFormat.PDF,
}

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_DOCX_BODY = "word/document.xml"
_DOCX_MAX_XML_BYTES = 64 * 1024 * 1024
"""Uncompressed ceiling for document.xml; rejects zip bombs before inflating."""


class ExtractionError(ValueError):
    """The upload is a supported format but its content cannot be read."""


class UnsupportedFormatError(ExtractionError):
    """The upload is not PDF, DOCX, Markdown, or plain text."""


@dataclass(frozen=True, slots=True)
class ExtractedText:
    text: str
    format: DocumentFormat
    title: str | None = None
    page_offsets: tuple[int, ...] | None = None
    """Offset in `text` where each page starts (PDF only)."""

    @property
    def page_count(self) -> int | None:
        return len(self.page_offsets) if self.page_offsets is not None else None


def detect_format(
    data: bytes, *, filename: str | None = None, declared_type: str | None = None
) -> DocumentFormat:
    """Magic bytes win for binary formats; the client's claims only choose among text formats."""
    if data.startswith(_PDF_MAGIC):
        return DocumentFormat.PDF
    if data.startswith(_ZIP_MAGIC):
        if _is_docx(data):
            return DocumentFormat.DOCX
        raise UnsupportedFormatError("ZIP archives other than DOCX are not supported")
    if b"\x00" in data[:8192] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UnsupportedFormatError("binary content is not a supported document format")

    claimed = _claimed_format(filename, declared_type)
    if claimed in (DocumentFormat.PDF, DocumentFormat.DOCX):
        raise ExtractionError(f"content does not look like {claimed.name}")
    return claimed or DocumentFormat.TEXT


def _claimed_format(filename: str | None, declared_type: str | None) -> DocumentFormat | None:
    if declared_type:
        mime = declared_type.split(";", 1)[0].strip().lower()
        if mime in _MIME_ALIASES:
            return _MIME_ALIASES[mime]
        try:
            return DocumentFormat(mime)
        except ValueError:
            pass
    if filename:
        return _EXTENSIONS.get(PurePath(filename).suffix.lower())
    return None


def _is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return _DOCX_BODY in archive.namelist()
    except zipfile.BadZipFile:
        return False


def extract(data: bytes, fmt: DocumentFormat) -> ExtractedText:
    match fmt:
        case DocumentFormat.PDF:
            return _extract_pdf(data)
        case DocumentFormat.DOCX:
            return _extract_docx(data)
        case DocumentFormat.MARKDOWN:
            return _extract_markdown(decode_text(data))
        case DocumentFormat.TEXT:
            return ExtractedText(normalize(decode_text(data)), fmt)


def extract_text(text: str, fmt: DocumentFormat) -> ExtractedText:
    """For content submitted as a string rather than a file."""
    if fmt is DocumentFormat.MARKDOWN:
        return _extract_markdown(text)
    if fmt is DocumentFormat.TEXT:
        return ExtractedText(normalize(text), fmt)
    raise UnsupportedFormatError(f"{fmt.name} content must be uploaded as a file")


def decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Legacy Windows exports; `replace` keeps ingestion going on stray bytes.
        return data.decode("cp1252", errors="replace")


# --- Normalization -------------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200d\u2060\ufeff]")
_INLINE_SPACE_RUN = re.compile(r"(?<=\S)[ \t\u00a0]{2,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_BLANK_LINES = re.compile(r"\n{3,}")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")


def normalize(text: str, *, dehyphenate: bool = False) -> str:
    """Canonical text: NFKC, LF newlines, no control characters, collapsed spacing.

    Leading indentation survives (it carries meaning in code and lists); runs of
    spaces inside a line and blank-line runs are collapsed.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text).replace("\t", "    ")
    if dehyphenate:
        text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _INLINE_SPACE_RUN.sub(" ", text)
    text = _TRAILING_SPACE.sub("\n", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def join_pages(pages: list[str]) -> tuple[str, tuple[int, ...]]:
    """Joins normalized pages with blank lines, recording where each page starts."""
    offsets: list[int] = []
    parts: list[str] = []
    cursor = 0
    for page in pages:
        if parts:
            parts.append("\n\n")
            cursor += 2
        offsets.append(cursor)
        parts.append(page)
        cursor += len(page)
    return "".join(parts), tuple(offsets)


# --- PDF -----------------------------------------------------------------------------------------


def _extract_pdf(data: bytes) -> ExtractedText:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionError("PDF is password protected")
        pages = [normalize(page.extract_text() or "", dehyphenate=True) for page in reader.pages]
        title = reader.metadata.title if reader.metadata else None
    except ExtractionError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, OSError) as exc:
        raise ExtractionError(f"unreadable PDF: {exc}") from exc

    text, offsets = join_pages(pages)
    if not text.strip():
        raise ExtractionError("PDF has no extractable text (scanned images need OCR)")
    return ExtractedText(text, DocumentFormat.PDF, _clean_title(title), offsets)


# --- DOCX ----------------------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DC_TITLE = "{http://purl.org/dc/elements/1.1/}title"
_HEADING_STYLE = re.compile(r"^(?:heading|titre|überschrift)\s*(\d)$", re.IGNORECASE)


def _extract_docx(data: bytes) -> ExtractedText:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo(_DOCX_BODY)
            if info.file_size > _DOCX_MAX_XML_BYTES:
                raise ExtractionError("DOCX body is too large")
            body = safe_xml.fromstring(archive.read(info))
            title = _docx_title(archive)
    except ExtractionError:
        raise
    except (
        zipfile.BadZipFile,
        KeyError,
        ElementTree.ParseError,
        DefusedXmlException,
        OSError,
    ) as exc:
        raise ExtractionError(f"unreadable DOCX: {exc}") from exc

    blocks = [block for block in _docx_blocks(body) if block.strip()]
    text = normalize("\n\n".join(blocks))
    if not text:
        raise ExtractionError("DOCX has no text")
    return ExtractedText(text, DocumentFormat.DOCX, title)


def _docx_title(archive: zipfile.ZipFile) -> str | None:
    try:
        core = safe_xml.fromstring(archive.read("docProps/core.xml"))
    except (KeyError, ElementTree.ParseError, DefusedXmlException):
        return None
    element = core.find(_DC_TITLE)
    return _clean_title(element.text if element is not None else None)


def _docx_blocks(root: ElementTree.Element) -> list[str]:
    body = root.find(f"{_W}body")
    if body is None:
        return []
    blocks: list[str] = []
    for element in body:
        if element.tag == f"{_W}p":
            blocks.append(_docx_paragraph(element))
        elif element.tag == f"{_W}tbl":
            blocks.append(_docx_table(element))
    return blocks


def _docx_paragraph(paragraph: ElementTree.Element) -> str:
    text = _docx_runs(paragraph)
    if not text.strip():
        return ""
    properties = paragraph.find(f"{_W}pPr")
    if properties is not None:
        style = properties.find(f"{_W}pStyle")
        name = style.get(f"{_W}val", "") if style is not None else ""
        # Headings become Markdown headings so the chunker sees the same structure.
        if name.lower() == "title":
            return f"# {text.strip()}"
        heading = _HEADING_STYLE.match(name)
        if heading:
            return f"{'#' * min(int(heading.group(1)), 6)} {text.strip()}"
        if properties.find(f"{_W}numPr") is not None:
            return f"- {text.strip()}"
    return text


def _docx_runs(element: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in element.iter():
        if node.tag == f"{_W}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{_W}tab":
            parts.append(" ")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
    return "".join(parts)


def _docx_table(table: ElementTree.Element) -> str:
    rows = []
    for row in table.iter(f"{_W}tr"):
        cells = [" ".join(_docx_runs(cell).split()) for cell in row.iter(f"{_W}tc")]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


# --- Markdown ------------------------------------------------------------------------------------

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n(?:---|\.\.\.)\n", re.DOTALL)
_FRONT_MATTER_TITLE = re.compile(r"^title:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _extract_markdown(raw: str) -> ExtractedText:
    """Keeps heading and list syntax (the chunker uses it) but drops link and image markup."""
    text = raw.replace("\r\n", "\n")
    title: str | None = None
    front = _FRONT_MATTER.match(text)
    if front:
        match = _FRONT_MATTER_TITLE.search(front.group(1))
        title = match.group(1) if match else None
        text = text[front.end() :]
    text = _HTML_COMMENT.sub("", text)
    text = _IMAGE.sub(lambda m: m.group(1), text)
    text = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    text = normalize(text)
    if title is None:
        heading = _H1.search(text)
        title = heading.group(1) if heading else None
    return ExtractedText(text, DocumentFormat.MARKDOWN, _clean_title(title))


def _clean_title(title: str | None) -> str | None:
    if not title:
        return None
    cleaned = " ".join(title.split())[:512]
    return cleaned or None
