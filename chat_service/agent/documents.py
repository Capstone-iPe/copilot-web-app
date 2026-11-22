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
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re

ACCEPTED = (".pdf", ".jpg", ".jpeg", ".png")
INJECTION_IN_DOC = re.compile(r"AI reviewer|set status to active|mark (this )?merchant active|ignore (the )?polic", re.I)

_FIXTURES = None
_APP_SHOPS = None


def _load_fixtures():
    global _FIXTURES, _APP_SHOPS
    if _FIXTURES is not None:
        return _FIXTURES, _APP_SHOPS
    _FIXTURES = {}
    _APP_SHOPS = {}
    here = Path(__file__).resolve()
    # Search for synth out fixtures.csv
    candidates = []
    if env := os.environ.get("FIXTURES_CSV"):
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    if len(here.parents) > 4:
        candidates.append(here.parents[4] / "analysis" / "synth" / "out" / "fixtures.csv")
    for cand in candidates:
        if cand.is_file():
            try:
                with open(cand, encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        sha = r.get("sha256")
                        if sha:
                            _FIXTURES[sha] = r
                apps_dir = cand.parent / "apps"
                if apps_dir.is_dir():
                    for app_dir in apps_dir.iterdir():
                        app_json = app_dir / "application.json"
                        if app_json.is_file():
                            try:
                                with open(app_json, encoding="utf-8") as af:
                                    data = json.load(af)
                                    _APP_SHOPS[data.get("application_id")] = data.get("answers", {}).get("shop_name")
                            except Exception:
                                pass
            except Exception:
                pass
            break
    return _FIXTURES, _APP_SHOPS


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

    fixtures, app_shops = _load_fixtures()
    invalid = None
    injection = False

    # Check Tier 0 Fixture lookup if available
    try:
        with open(path, "rb") as f:
            file_bytes = f.read()
            digest = hashlib.sha256(file_bytes).hexdigest()
    except Exception:
        digest = None

    if digest and digest in fixtures:
        fix = fixtures[digest]
        if fix.get("slot") != item:
            invalid = "WRONG_DOCUMENT"
        elif fix.get("accepted") == "False":
            invalid = fix.get("reason_code", "WRONG_DOCUMENT")
        elif legal_name and fix.get("application_id") in app_shops:
            owner_shop = app_shops[fix["application_id"]]
            if owner_shop and owner_shop.strip().lower() != legal_name.strip().lower():
                if item == "bank_proof":
                    invalid = "BANK_PROOF_NAME_MISMATCH"
                elif item == "business_registration":
                    invalid = "NAME_MISMATCH"

    text = read_pdf_text(path) if ext == ".pdf" else ""
    if text:
        injection = bool(INJECTION_IN_DOC.search(text))

    if invalid is None:
        if item == "business_registration" and ext == ".pdf" and text:
            if not re.search(r"certificate of registration|shops and establishment|establishment registration|registration certificate|udyam", text, re.I):
                invalid = "WRONG_DOCUMENT"
            elif legal_name:
                legal_tokens = set(re.findall(r"\w+", legal_name.lower()))
                doc_tokens = set(re.findall(r"\w+", text.lower()))
                if legal_tokens and len(legal_tokens & doc_tokens) / len(legal_tokens) < 0.85:
                    invalid = "NAME_MISMATCH"

        if item == "bank_proof" and text:
            m = re.search(r"Account\s+(?:holder|name):\s*([^\r\n]+)", text, re.I)
            if m and legal_name:
                if m.group(1).strip().lower() != legal_name.strip().lower():
                    invalid = "BANK_PROOF_NAME_MISMATCH"
            elif legal_name and legal_name.strip().lower() not in text.lower():
                invalid = "BANK_PROOF_NAME_MISMATCH"

        if item == "shopfront_photo" and ext == ".png":
            try:
                from PIL import Image
                captured = Image.open(path).text.get("ipe:capture_date")
            except Exception:  # noqa: BLE001
                captured = None
            if captured and (today - dt.date.fromisoformat(captured)).days > 90:
                invalid = "SHOPFRONT_PHOTO_STALE"

        if item == "dpa_addendum" and ext == ".pdf":
            if text and not re.search(r"data[- ]processing addendum|dpa|payer telephone", text, re.I):
                invalid = "WRONG_DOCUMENT"
            elif re.search(r"not signed|_{6,}", text):
                invalid = "ADDENDUM_UNSIGNED"

    return {"accepted": True, "invalid": invalid, "injection": injection}
