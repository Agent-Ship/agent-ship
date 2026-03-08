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


@router.post("/upload")
async def upload_tax_document(file: UploadFile = File(...)):
    """
    Accept a tax document (PDF or text), extract its text, and return it.
    The frontend sends extracted text to the tax_filing_agent for analysis.
    """
    content_type = file.content_type or ""
    raw = await file.read()

    if len(raw) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB)")

    # ── PDF ─────────────────────────────────────────────────────────────────
    if content_type == "application/pdf" or file.filename.lower().endswith(".pdf"):
        result = pdf_utils.extract_text_from_bytes(raw)
        if "error" in result:
            raise HTTPException(status_code=422, detail=f"Could not read PDF: {result['error']}")
        text = result.get("extracted_text", "")
        if not text.strip():
            raise HTTPException(
                status_code=422,
                detail="No text found in PDF. The document may be scanned/image-based.",
            )
        return JSONResponse({"extracted_text": text, "file_name": file.filename, "pages": result.get("page_count")})

    # ── Plain text ───────────────────────────────────────────────────────────
    if content_type.startswith("text/") or file.filename.lower().endswith(".txt"):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not decode text file: {e}")
        return JSONResponse({"extracted_text": text, "file_name": file.filename})

    # ── Images (not supported without OCR) ───────────────────────────────────
    if content_type.startswith("image/"):
        raise HTTPException(
            status_code=422,
            detail=(
                "Image files require OCR which is not enabled in this demo. "
                "Please export your document as a PDF and upload that instead."
            ),
        )

    raise HTTPException(
        status_code=415,
        detail=f"Unsupported file type '{content_type}'. Please upload a PDF or text file.",
    )
