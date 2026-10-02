"""Render PDF pages and make irreversible content redactions."""

from __future__ import annotations

import pymupdf

from app.models import AddedText, Redaction, TextReplacement


class PdfEditError(ValueError):
    pass


def prepare_pdf(data: bytes, password: str) -> tuple[bytes, list[dict[str, float]]]:
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass and not document.authenticate(password):
                raise PdfEditError("A valid PDF password is required")
            if document.page_count == 0 or document.page_count > 200:
                raise PdfEditError("PDF must have between 1 and 200 pages")
            pages = [
                {"width": page.rect.width, "height": page.rect.height}
                for page in document
            ]
            # Store a decrypted working copy so preview and save never need the password.
            return document.tobytes(garbage=4, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE), pages
    except PdfEditError:
        raise
    except Exception as exc:
        raise PdfEditError("Invalid PDF file") from exc


def render_page(data: bytes, page_number: int) -> bytes:
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if page_number < 0 or page_number >= document.page_count:
            raise PdfEditError("Page not found")
        page = document[page_number]
        scale = min(2.0, 2000 / max(page.rect.width, 1), 2600 / max(page.rect.height, 1))
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png")


def _standard_font_name(name: str) -> str:
    lower = name.lower()
    family = "cour" if "cour" in lower or "mono" in lower else "tiro" if "times" in lower or "serif" in lower else "helv"
    bold = "bold" in lower
    italic = "italic" in lower or "oblique" in lower
    if family == "cour":
        return "cobi" if bold and italic else "cobo" if bold else "coit" if italic else "cour"
    if family == "tiro":
        return "tibi" if bold and italic else "tibo" if bold else "tiit" if italic else "tiro"
    return "hebi" if bold and italic else "hebo" if bold else "heit" if italic else "helv"


def _text_spans(page: pymupdf.Page) -> list[dict]:
    spans = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if not span["text"].strip():
                    continue
                rect = pymupdf.Rect(span["bbox"])
                if rect.is_empty:
                    continue
                visible = rect * page.rotation_matrix
                origin = pymupdf.Point(span["origin"]) * page.rotation_matrix
                spans.append({
                    "span_id": len(spans),
                    "text": span["text"],
                    "x0": visible.x0, "y0": visible.y0,
                    "x1": visible.x1, "y1": visible.y1,
                    "origin_x": origin.x, "origin_y": origin.y,
                    "font_size": span["size"],
                    "font_name": _standard_font_name(span["font"]),
                    "color": f"#{span['color'] & 0xffffff:06x}",
                    "_rect": rect,
                    "_origin": pymupdf.Point(span["origin"]),
                })
    return spans


def get_text_spans(data: bytes, page_number: int) -> list[dict]:
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if page_number < 0 or page_number >= document.page_count:
            raise PdfEditError("Page not found")
        return [{key: value for key, value in span.items() if not key.startswith("_")}
                for span in _text_spans(document[page_number])]


def _rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))


def _rect_on_page(page: pymupdf.Page, edit: Redaction) -> pymupdf.Rect:
    visible = page.rect
    if not (0 <= edit.x0 < edit.x1 <= visible.width and 0 <= edit.y0 < edit.y1 <= visible.height):
        raise PdfEditError("Redaction is outside the page")
    return pymupdf.Rect(edit.x0, edit.y0, edit.x1, edit.y1) * page.derotation_matrix


def _point_on_page(page: pymupdf.Page, edit: AddedText) -> pymupdf.Point:
    visible = page.rect
    if not (0 <= edit.x <= visible.width and 0 <= edit.y <= visible.height):
        raise PdfEditError("Text position is outside the page")
    return pymupdf.Point(edit.x, edit.y) * page.derotation_matrix


def apply_edits(
    data: bytes, redactions: list[Redaction], texts: list[AddedText], replacements: list[TextReplacement] | None = None
) -> bytes:
    replacements = replacements or []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        page_count = document.page_count
        if any(edit.page >= page_count for edit in [*redactions, *texts, *replacements]):
            raise PdfEditError("Page not found")

        redaction_rects = [(edit.page, _rect_on_page(document[edit.page], edit)) for edit in redactions]

        replacement_spans: list[tuple[TextReplacement, dict]] = []
        spans_by_page: dict[int, list[dict]] = {}
        used_spans: set[tuple[int, int]] = set()
        for edit in replacements:
            if (edit.page, edit.span_id) in used_spans:
                raise PdfEditError("The same text was selected more than once")
            used_spans.add((edit.page, edit.span_id))
            page = document[edit.page]
            if edit.page not in spans_by_page:
                spans_by_page[edit.page] = _text_spans(page)
            spans = spans_by_page[edit.page]
            if edit.span_id >= len(spans):
                raise PdfEditError("Selected text was not found")
            span = spans[edit.span_id]
            page.add_redact_annot(span["_rect"], fill=(1, 1, 1))
            replacement_spans.append((edit, span))

        for page in document:
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )

        for edit in texts:
            page = document[edit.page]
            point = _point_on_page(page, edit)
            try:
                page.insert_text(
                    point, edit.text, fontsize=edit.font_size, fontname=edit.font_name, color=_rgb(edit.color),
                    morph=(point, pymupdf.Matrix(edit.scale_x, edit.scale_y) * pymupdf.Matrix(page.rotation)),
                )
            except Exception as exc:
                raise PdfEditError("Could not add text at this position") from exc

        for edit, span in replacement_spans:
            if not edit.text:
                continue
            try:
                document[edit.page].insert_text(
                    span["_origin"], edit.text,
                    fontsize=edit.font_size or span["font_size"],
                    fontname=edit.font_name or span["font_name"],
                    color=_rgb(edit.color or span["color"]),
                    rotate=document[edit.page].rotation,
                )
            except Exception as exc:
                raise PdfEditError("Could not replace the selected text") from exc

        for page_number, rectangle in redaction_rects:
            document[page_number].add_redact_annot(rectangle, fill=(0, 0, 0))
        for page_number in {page_number for page_number, _ in redaction_rects}:
            document[page_number].apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )

        return document.tobytes(garbage=4, deflate=True, clean=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
