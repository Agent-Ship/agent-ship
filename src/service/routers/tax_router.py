"""Tax demo — file upload endpoint that extracts text from tax documents."""

import logging
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from src.agent_framework.utils.pdf_utils import PdfUtils

logger = logging.getLogger(__name__)
router = APIRouter()
pdf_utils = PdfUtils()

SUPPORTED_TYPES = {
    "application/pdf",
    "text/plain",
    "image/png",
    "image/jpeg",
    "image/gif",
}

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


def _is_pdf_file(file: UploadFile) -> bool:
    """Return True if file appears to be a PDF (by content-type or extension)."""
    ct = (file.content_type or "").lower()
    fn = (file.filename or "").lower()
    return ct == "application/pdf" or fn.endswith(".pdf")


@router.post("/upload")
async def upload_tax_document(file: UploadFile = File(...)):
    """
    Accept a tax document (PDF or text), extract its text, and return it.
    The frontend sends extracted text to the tax_filing_agent for analysis.
    """
    content_type = (file.content_type or "").strip()
    filename = file.filename or "unknown"
    raw = await file.read()

    logger.info("Tax upload: filename=%r content_type=%r size=%d", filename, content_type, len(raw))

    if len(raw) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB)")

    # ── PDF ─────────────────────────────────────────────────────────────────
    if _is_pdf_file(file):
        result = pdf_utils.extract_text_from_bytes(raw)
        if "error" in result:
            logger.warning("PDF extraction failed: %s", result["error"])
            raise HTTPException(status_code=422, detail=f"Could not read PDF: {result['error']}")
        text = result.get("extracted_text", "")
        if not text.strip():
            logger.warning("PDF extracted empty text (possibly scanned/image-based): %r", filename)
            raise HTTPException(
                status_code=422,
                detail=(
                    "No text found in PDF. Scanned or image-based W-2s are not supported. "
                    "Try a digital PDF from your employer, or copy the text into a .txt file and upload that."
                ),
            )
        return JSONResponse({"extracted_text": text, "file_name": filename, "pages": result.get("page_count")})

    # ── Plain text ───────────────────────────────────────────────────────────
    if content_type.startswith("text/") or filename.lower().endswith(".txt"):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not decode text file: {e}")
        return JSONResponse({"extracted_text": text, "file_name": filename})

    # ── Images (not supported without OCR) ───────────────────────────────────
    if content_type.startswith("image/"):
        raise HTTPException(
            status_code=422,
            detail=(
                "Image files require OCR which is not enabled in this demo. "
                "Please export your document as a PDF and upload that instead."
            ),
        )

    logger.warning("Unsupported upload: filename=%r content_type=%r", filename, content_type)
    raise HTTPException(
        status_code=415,
        detail=f"Unsupported file type '{content_type or '(unknown)'}'. Please upload a PDF or .txt file.",
    )
