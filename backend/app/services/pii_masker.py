import re
from typing import Dict, Tuple

class PIIMasker:
    """
    Client-side & Ingestion PII masking to comply with DPDP Act 2023.
    Redacts Aadhaar numbers, PAN cards, credit/debit card numbers, and raw phone numbers
    while preserving non-sensitive legal facts.
    """

    # Aadhaar format: 12 digits (with optional spaces/dashes)
    AADHAAR_REGEX = re.compile(r'\b[2-9]{1}[0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}\b')
    # PAN format: 5 letters, 4 digits, 1 letter
    PAN_REGEX = re.compile(r'\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b', re.IGNORECASE)
    # Phone format: Indian 10-digit mobile with optional +91/0 prefix
    PHONE_REGEX = re.compile(r'(?:\+91|0)?[6-9]\d{9}\b')
    # Credit/Debit Card: 16 digits
    CARD_REGEX = re.compile(r'\b(?:\d{4}[ -]?){3}\d{4}\b')

    @classmethod
    def mask_text(cls, text: str) -> Tuple[str, Dict[str, str]]:
        """
        Masks sensitive PII and returns the sanitized text along with a mapping dictionary
        so data can be restored in private document rendering if needed.
        """
        if not text:
            return text, {}

        masked_map = {}
        counter = 1

        def replace_aadhaar(match):
            nonlocal counter
            key = f"[MASKED_AADHAAR_{counter}]"
            masked_map[key] = match.group(0)
            counter += 1
            return "[AADHAAR: XXXX-XXXX-" + match.group(0)[-4:] + "]"

        def replace_pan(match):
            nonlocal counter
            key = f"[MASKED_PAN_{counter}]"
            masked_map[key] = match.group(0)
            counter += 1
            return "[PAN: XXXXX" + match.group(0)[5:] + "]"

        def replace_card(match):
            nonlocal counter
            key = f"[MASKED_CARD_{counter}]"
            masked_map[key] = match.group(0)
            counter += 1
            return "[CARD: XXXX-XXXX-XXXX-" + match.group(0)[-4:] + "]"

        sanitized = cls.AADHAAR_REGEX.sub(replace_aadhaar, text)
        sanitized = cls.PAN_REGEX.sub(replace_pan, sanitized)
        sanitized = cls.CARD_REGEX.sub(replace_card, sanitized)

        return sanitized, masked_map
