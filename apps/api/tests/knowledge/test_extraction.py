import io
import zipfile

import pytest

from cortex_api.services.knowledge.extraction import (
    DocumentFormat,
    ExtractionError,
    UnsupportedFormatError,
    decode_text,
    detect_format,
    extract,
    extract_text,
    join_pages,
    normalize,
)

from .samples import docx_paragraph, docx_table, make_docx, make_pdf


class TestDetectFormat:
    def test_magic_bytes_beat_client_claims(self) -> None:
        pdf = make_pdf([["hello"]])
        assert detect_format(pdf, filename="notes.txt", declared_type="text/plain") is (
            DocumentFormat.PDF
        )
        docx = make_docx([docx_paragraph("hello")])
        assert detect_format(docx, filename="x.bin") is DocumentFormat.DOCX

    @pytest.mark.parametrize(
        ("filename", "declared", "expected"),
        [
            ("guide.md", None, DocumentFormat.MARKDOWN),
            ("guide.markdown", None, DocumentFormat.MARKDOWN),
            (None, "text/markdown; charset=utf-8", DocumentFormat.MARKDOWN),
            (None, "text/x-markdown", DocumentFormat.MARKDOWN),
            ("notes.txt", None, DocumentFormat.TEXT),
            ("README", None, DocumentFormat.TEXT),
            (None, None, DocumentFormat.TEXT),
        ],
    )
    def test_text_formats_follow_claims(
        self, filename: str | None, declared: str | None, expected: DocumentFormat
    ) -> None:
        assert detect_format(b"# Title\nbody", filename=filename, declared_type=declared) is (
            expected
        )

    def test_rejects_other_binaries_and_archives(self) -> None:
        with pytest.raises(UnsupportedFormatError):
            detect_format(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", filename="a.png")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("data.csv", "a,b")
        with pytest.raises(UnsupportedFormatError, match="ZIP"):
            detect_format(archive.getvalue(), filename="data.zip")

    def test_text_claiming_to_be_binary_format_is_rejected(self) -> None:
        with pytest.raises(ExtractionError, match="PDF"):
            detect_format(b"just text", filename="report.pdf")


class TestNormalize:
    def test_canonicalizes_whitespace_and_unicode(self) -> None:
        raw = "\uff26\uff55\uff4c\uff4c\u200bwidth\r\nline  with   gaps \t\n\n\n\nnext\x07 para  "
        assert normalize(raw) == "Fullwidth\nline with gaps\n\nnext para"

    def test_keeps_leading_indentation(self) -> None:
        assert normalize("code:\n    indented   line") == "code:\n    indented line"

    def test_dehyphenates_only_when_asked(self) -> None:
        assert normalize("infor-\nmation") == "infor-\nmation"
        assert normalize("infor-\nmation", dehyphenate=True) == "information"

    def test_join_pages_records_offsets(self) -> None:
        text, offsets = join_pages(["one", "two", "three"])
        assert text == "one\n\ntwo\n\nthree"
        assert offsets == (0, 5, 10)
        assert [text[o : o + 3] for o in offsets] == ["one", "two", "thr"]

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ("héllo".encode(), "héllo"),
            (b"\xef\xbb\xbfbom", "bom"),
            ("utf16 ✓".encode("utf-16"), "utf16 ✓"),
            (b"caf\xe9", "café"),  # cp1252 fallback
        ],
    )
    def test_decode_text(self, data: bytes, expected: str) -> None:
        assert decode_text(data) == expected


class TestMarkdown:
    def test_front_matter_title_links_and_images(self) -> None:
        raw = (
            "---\ntitle: 'Refund Policy'\nowner: billing\n---\n"
            "# Heading\n<!-- internal -->See [the docs](https://x.test/docs) "
            "![diagram](d.png) for more.\n"
        )
        result = extract(raw.encode(), DocumentFormat.MARKDOWN)
        assert result.title == "Refund Policy"
        assert result.text == "# Heading\nSee the docs (https://x.test/docs) diagram for more."
        assert result.page_offsets is None

    def test_title_falls_back_to_first_h1(self) -> None:
        result = extract_text("intro\n\n# Getting Started #\ntext", DocumentFormat.MARKDOWN)
        assert result.title == "Getting Started"

    def test_binary_formats_cannot_be_submitted_as_text(self) -> None:
        with pytest.raises(UnsupportedFormatError):
            extract_text("x", DocumentFormat.PDF)


class TestPdf:
    def test_extracts_pages_title_and_offsets(self) -> None:
        pdf = make_pdf(
            [["Quarterly report", "Revenue grew 12 percent."], ["Page two covers risks."]],
            title="Q3 Report",
        )
        result = extract(pdf, DocumentFormat.PDF)
        assert result.format is DocumentFormat.PDF
        assert result.title == "Q3 Report"
        assert result.page_count == 2
        assert result.page_offsets is not None
        second = result.text[result.page_offsets[1] :]
        assert second.startswith("Page two covers risks.")
        assert "Revenue grew 12 percent." in result.text[: result.page_offsets[1]]

    def test_corrupt_pdf_is_an_extraction_error(self) -> None:
        with pytest.raises(ExtractionError, match="PDF"):
            extract(b"%PDF-1.4\nthis is not really a pdf", DocumentFormat.PDF)

    def test_pdf_without_text_needs_ocr(self) -> None:
        with pytest.raises(ExtractionError, match="OCR"):
            extract(make_pdf([[]]), DocumentFormat.PDF)


class TestDocx:
    def test_structure_becomes_markdown(self) -> None:
        docx = make_docx(
            [
                docx_paragraph("Employee Handbook", style="Title"),
                docx_paragraph("Leave", style="Heading1"),
                docx_paragraph("Everyone gets 25 days."),
                docx_paragraph("Carry over up to 5 days", numbered=True),
                docx_table([["Region", "Days"], ["EU", "25"], ["US", "20"]]),
                docx_paragraph("   "),
            ],
            title="Handbook 2026",
        )
        result = extract(docx, DocumentFormat.DOCX)
        assert result.title == "Handbook 2026"
        assert result.text == (
            "# Employee Handbook\n\n# Leave\n\nEveryone gets 25 days.\n\n"
            "- Carry over up to 5 days\n\nRegion | Days\nEU | 25\nUS | 20"
        )

    def test_entity_expansion_is_refused(self) -> None:
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>&lol2;</w:t></w:r></w:p></w:body></w:document>"
        )
        with pytest.raises(ExtractionError, match="unreadable DOCX"):
            extract(make_docx([], raw_body=bomb), DocumentFormat.DOCX)

    def test_empty_docx(self) -> None:
        with pytest.raises(ExtractionError, match="no text"):
            extract(make_docx([docx_paragraph(" ")]), DocumentFormat.DOCX)
