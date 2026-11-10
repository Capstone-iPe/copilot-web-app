"""Checks on an uploaded document. Pure functions, no AI.

The checks follow ONB-POL-001 §2 and ONB-POL-003 §2:
  - file type must be PDF, JPG or PNG
  - bank proof must be in the shop's legal name
  - shopfront photo must be from the last 90 days (when the photo carries a date)
  - the data-processing addendum must be signed
  - a business registration must actually be a registration
It also spots text inside a document that tries to give the reviewer instructions
(ONB-POL-003 §3), so the agent can report that it was ignored.
"""
import datetime as dt
import os
import re

ACCEPTED = (".pdf", ".jpg", ".jpeg", ".png")
INJECTION_IN_DOC = re.compile(r"AI reviewer|set status to active|mark (this )?merchant active|ignore (the )?polic", re.I)


def read_pdf_text(path):
    from pypdf import PdfReader
    try:
        return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    except Exception:  # noqa: BLE001  unreadable PDF: treat as no text
        return ""


def check_document(item, path, filename, today, legal_name):
    """Returns dict(accepted: bool, invalid: str|None, injection: bool)."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ACCEPTED:
        return {"accepted": False, "invalid": None, "injection": False}
    text = read_pdf_text(path) if ext == ".pdf" else ""
    injection = bool(INJECTION_IN_DOC.search(text))
    invalid = None
    if item == "business_registration" and ext == ".pdf" and text and "Certificate of Registration" not in text:
        invalid = "WRONG_DOCUMENT"
    if item == "bank_proof" and text:
        m = re.search(r"Account holder:\s*(.+)", text)
        if m and legal_name and m.group(1).strip().lower() != legal_name.strip().lower():
            invalid = "BANK_PROOF_NAME_MISMATCH"
    if item == "shopfront_photo" and ext == ".png":
        try:
            from PIL import Image
            captured = Image.open(path).text.get("ipe:capture_date")
        except Exception:  # noqa: BLE001
            captured = None
        if captured and (today - dt.date.fromisoformat(captured)).days > 90:
            invalid = "SHOPFRONT_PHOTO_STALE"
    if item == "dpa_addendum" and ext == ".pdf" and re.search(r"not signed|_{6,}", text):
        invalid = "ADDENDUM_UNSIGNED"
    return {"accepted": True, "invalid": invalid, "injection": injection}
