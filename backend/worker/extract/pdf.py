"""PDF text per page (PyMuPDF), OCR for image-only pages and for embedded screenshots (Tesseract)."""
import io
import logging
from typing import Callable

import pymupdf as fitz
import pytesseract
from PIL import Image

from worker.extract import Extracted, ExtractionError, Segment

log = logging.getLogger("infopoint.extract.pdf")

MIN_PAGE_CHARS = 50          # below this a page is treated as scanned and OCR'd
MIN_IMAGE_W, MIN_IMAGE_H = 200, 80
MAX_IMAGES_PER_PAGE = 8


def _ocr(img: Image.Image, langs: str) -> str:
    try:
        return pytesseract.image_to_string(img, lang=langs, config="--psm 3").strip()
    except pytesseract.TesseractError as e:
        log.warning("tesseract failed: %s", e)
        return ""


def _ocr_page(page: "fitz.Page", langs: str) -> str:
    pix = page.get_pixmap(dpi=300, colorspace=fitz.csGRAY)
    return _ocr(Image.open(io.BytesIO(pix.tobytes("png"))), langs)


def _ocr_images(doc: "fitz.Document", page: "fitz.Page", langs: str, cache: dict[int, str]) -> list[str]:
    texts = []
    for info in page.get_images(full=True)[:MAX_IMAGES_PER_PAGE]:
        xref, width, height = info[0], info[2], info[3]
        if width < MIN_IMAGE_W or height < MIN_IMAGE_H:
            continue
        if xref not in cache:  # logos repeat on every page; OCR each image once
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:  # CMYK etc.
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                cache[xref] = _ocr(Image.open(io.BytesIO(pix.tobytes("png"))), langs)
            except Exception as e:  # unusual colourspaces / broken streams: skip the image, keep the page
                log.debug("image %s skipped: %s", xref, e)
                cache[xref] = ""
        if len(cache[xref]) >= 3:
            texts.append(cache[xref])
    return texts


def extract_pdf(path: str, langs: str = "eng", loc_type: str = "page", ocr_images: bool = True,
                progress: Callable[[float, str], None] | None = None) -> Extracted:
    try:
        doc = fitz.open(path)
    except Exception as e:
        raise ExtractionError(f"Cannot open PDF: {e}") from e
    if doc.needs_pass:
        raise ExtractionError("PDF is password-protected.")
    if doc.page_count == 0:
        raise ExtractionError("PDF has no pages (damaged file?).")
    segments: list[Segment] = []
    image_cache: dict[int, str] = {}
    with doc:
        n = doc.page_count
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            if len(text) < MIN_PAGE_CHARS:
                ocr_text = _ocr_page(page, langs)
                text = ocr_text if len(ocr_text) > len(text) else text
            elif ocr_images:
                shots = _ocr_images(doc, page, langs, image_cache)
                if shots:
                    text += "\n\n" + "\n".join(f"[Screenshot text] {t}" for t in shots)
            if text:
                segments.append(Segment(text=text, loc_type=loc_type, page=i + 1))
            if progress and (i % 5 == 4 or i == n - 1):
                progress((i + 1) / n * 0.5, f"Reading page {i + 1} of {n}")
    return Extracted(segments=segments, page_count=n)
