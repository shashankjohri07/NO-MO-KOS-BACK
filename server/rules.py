"""Detect-mode rules — currently the dormant `check_rule_doc_upload`.

The PAGINATION rule lives in pagination.py (next to write_pagination).
This module exists for the document-presence keyword check, which is
only reachable through the CLI's `--mode detect` / `--mode both` paths;
the production UI doesn't surface it.

Pure module split — no logic changes.
"""

from typing import List


def check_rule_doc_upload(pages: List[dict]) -> dict:
    """All required sections must be present in the filing."""
    required_docs = {
        "Appeal/Petition": ["APPEAL", "PETITION", "COMP. APP", "COMPANY APPEAL"],
        "Impugned Order": ["IMPUGNED ORDER", "CERTIFIED COPY", "ORDER DATED", "ORDER DT"],
        "Affidavit": ["AFFIDAVIT", "SOLEMNLY AFFIRM", "DEPONENT", "SWORN"],
        "Vakalatnama": ["VAKALATNAMA", "VAKALAT", "MEMO OF APPEARANCE", "POWER OF ATTORNEY"],
        "Index": ["INDEX", "TABLE OF CONTENTS", "PARTICULARS"],
        "Annexures": ["ANNEXURE", "EXHIBIT", "ANNEXURE-"],
    }

    found = []
    missing = []
    found_details = []
    for doc_name, keywords in required_docs.items():
        found_on = []
        for page in pages:
            if any(kw in page["text_upper"] for kw in keywords):
                found_on.append(page["page_num"])
        if found_on:
            found.append(doc_name)
            found_details.append({"doc": doc_name, "pages": found_on[:3]})
        else:
            missing.append(doc_name)

    if not missing:
        detail_str = "; ".join(
            f"{d['doc']} (page {', '.join(str(p) for p in d['pages'])})"
            for d in found_details
        )
        return {
            "rule_id": "DOC_UPLOAD",
            "status": "pass",
            "severity": "high",
            "description": "All required documents must be in your filing",
            "detail": f"Good — all required documents are present. Found: {detail_str}.",
            "found": found,
        }

    return {
        "rule_id": "DOC_UPLOAD",
        "status": "fail" if len(missing) > 1 else "warning",
        "severity": "high",
        "description": "All required documents must be in your filing",
        "detail": (
            f"Some required documents are missing. Please add: {', '.join(missing)}. "
            f"You already have: {', '.join(found) if found else 'none'}."
        ),
        "missing": missing,
        "found": found,
    }
