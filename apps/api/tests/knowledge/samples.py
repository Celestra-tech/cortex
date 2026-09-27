"""Builds real PDF and DOCX bytes in memory so tests need no binary fixtures."""

import io
import zipfile
from collections.abc import Sequence
from xml.sax.saxutils import escape


def make_pdf(pages: Sequence[Sequence[str]], *, title: str | None = None) -> bytes:
    """A minimal valid PDF: one Helvetica text line per string, one page per sequence."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # placeholder, filled once the page tree number is known
    pages_ref = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_refs: list[int] = []
    for lines in pages:
        ops = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
        for line in lines:
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({safe}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        page_refs.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_ref, font, content)
            )
        )
    kids = b" ".join(b"%d 0 R" % ref for ref in page_refs)
    objects[pages_ref - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_refs))
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_ref
    info = add(b"<< /Title (%s) >>" % title.encode("latin-1")) if title else None

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (number, body))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    trailer = b"<< /Size %d /Root %d 0 R" % (len(objects) + 1, catalog)
    if info:
        trailer += b" /Info %d 0 R" % info
    out.write(b"trailer\n%s >>\nstartxref\n%d\n%%%%EOF\n" % (trailer, xref))
    return out.getvalue()


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def docx_paragraph(text: str, *, style: str | None = None, numbered: bool = False) -> str:
    properties = ""
    if style or numbered:
        inner = f'<w:pStyle w:val="{style}"/>' if style else ""
        if numbered:
            inner += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
        properties = f"<w:pPr>{inner}</w:pPr>"
    return f"<w:p>{properties}{_run(text)}</w:p>"


def docx_table(rows: Sequence[Sequence[str]]) -> str:
    body = "".join(
        "<w:tr>" + "".join(f"<w:tc>{docx_paragraph(cell)}</w:tc>" for cell in row) + "</w:tr>"
        for row in rows
    )
    return f"<w:tbl>{body}</w:tbl>"


def make_docx(
    blocks: Sequence[str], *, title: str | None = None, raw_body: str | None = None
) -> bytes:
    document = raw_body or (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>{"".join(blocks)}</w:body></w:document>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        archive.writestr("word/document.xml", document)
        if title:
            archive.writestr(
                "docProps/core.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
                'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                f"<dc:title>{escape(title)}</dc:title></cp:coreProperties>",
            )
    return out.getvalue()
