from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import re

ACCEPTED = (".pdf", ".jpg", ".jpeg", ".png")
INJECTION_IN_DOC = re.compile(
    r"AI reviewer|set status to active|mark (this )?merchant active|ignore (the )?polic",
    re.I,
)

_LEGAL_STOPWORDS = frozenset({
    "pvt", "private", "ltd", "limited", "llp", "and", "the",
    "co", "company", "enterprises", "traders"
})


def read_pdf_text(path: str | Path) -> str:
    from pypdf import PdfReader
    try:
        return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    except Exception:  # noqa: BLE001
        return ""


def read_image_text(path: str | Path) -> str:
    try:
        import pytesseract
        from PIL import Image
        return pytesseract.image_to_string(Image.open(path))
    except Exception:
        return ""


def _normalize_name_tokens(name: str) -> set[str]:
    cleaned = re.sub(r"[^\w\s]", " ", name.lower())
    tokens = set(cleaned.split())
    non_stop = tokens - _LEGAL_STOPWORDS
    return non_stop if non_stop else tokens


def _name_matches(legal_name: str, doc_text: str) -> bool:
    if not legal_name or not legal_name.strip():
        return True
    shop_tokens = _normalize_name_tokens(legal_name)
    doc_tokens = set(re.findall(r"\w+", doc_text.lower()))
    if not doc_tokens:
        return legal_name.strip().lower() in doc_text.lower()
    overlap = shop_tokens & doc_tokens
    return (len(overlap) / len(shop_tokens)) >= 0.85


def check_document(item: str, path: str, filename: str, today: dt.date, legal_name: str | None) -> dict:
    """Returns dict(accepted: bool, invalid: str|None, injection: bool)."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ACCEPTED:
        return {"accepted": False, "invalid": None, "injection": False}

    invalid = None
    injection = False

    text = ""
    if ext == ".pdf":
        text = read_pdf_text(path)
    elif ext in (".jpg", ".jpeg", ".png"):
        text = read_image_text(path)

    if text:
        injection = bool(INJECTION_IN_DOC.search(text))

    # 1. Shopfront photo date check
    if item == "shopfront_photo":
        captured_date = None
        if ext == ".png":
            try:
                from PIL import Image
                c_str = Image.open(path).text.get("ipe:capture_date")
                if c_str:
                    captured_date = dt.date.fromisoformat(c_str)
            except Exception:
                pass
        elif ext in (".jpg", ".jpeg"):
            try:
                from PIL import Image
                im = Image.open(path)
                exif = im.getexif()
                stamp = (exif.get_ifd(0x8769).get(0x9003) if exif else None) or (exif.get(0x0132) if exif else None)
                if stamp:
                    captured_date = dt.datetime.strptime(stamp, "%Y:%m:%d %H:%M:%S").date()
            except Exception:
                pass

        if not captured_date:
            # Fallback to date in filename
            date_matches = re.findall(r"\d{4}-\d{2}-\d{2}", filename)
            if date_matches:
                try:
                    captured_date = dt.date.fromisoformat(date_matches[0])
                except Exception:
                    pass

        if captured_date and (today - captured_date).days > 90:
            invalid = "SHOPFRONT_PHOTO_STALE"

    # 2. Business registration checks
    elif item == "business_registration":
        if text:
            if not re.search(r"certificate of registration|shops and establishment|establishment registration|registration certificate|udyam|reg_pdf|form c|business_registration", text, re.I):
                invalid = "WRONG_DOCUMENT"
            elif legal_name and not _name_matches(legal_name, text):
                invalid = "NAME_MISMATCH"

    # 3. Bank proof checks
    elif item == "bank_proof":
        if text:
            if not re.search(r"account|bank|cheque|passbook|statement|branch|ifsc", text, re.I):
                invalid = "WRONG_DOCUMENT"
            elif legal_name and not _name_matches(legal_name, text):
                invalid = "BANK_PROOF_NAME_MISMATCH"

    # 4. Data-processing addendum checks
    elif item == "dpa_addendum":
        if text:
            if not re.search(r"data[- ]processing addendum|dpa|payer telephone", text, re.I):
                invalid = "WRONG_DOCUMENT"
            elif re.search(r"\bnot signed\b|unsigned|_{6,}", text, re.I):
                invalid = "ADDENDUM_UNSIGNED"
            elif legal_name and not _name_matches(legal_name, text):
                invalid = "NAME_MISMATCH"

    return {"accepted": True, "invalid": invalid, "injection": injection}
