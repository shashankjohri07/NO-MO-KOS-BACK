"""
NCLT (National Company Law Tribunal) Document Formatting Rules

Based on OFFICIAL sources:
1. National Company Law Tribunal Rules, 2016 (NCLT Rules 2016) - Gazette of India
   https://ca2013.com/wp-content/uploads/2016/08/National-Company-Law-Tribunal-Rules-2016-dated-21.07.2016_1.pdf
2. NCLT Rules 2016 - Rules 20-44 (Format, Procedure, Filing)
3. NCLT Rules 2016 - Rules 125-130 (Affidavit Requirements)
4. NCLT Rules 2016 - Rule 112 (Schedule of Fees)
5. NCLT Principal Bench Order dt. 28.09.2023 (E-filing: OCR, bookmarking, DSC)
6. NCLT Principal Bench Order dt. 22.12.2023 (Mandatory e-filing from 01.01.2024)
7. NCLT Common Defect Lists (published on nclt.gov.in)
8. NCLT Filing Guide - ncltadvocates.com
9. NCLT Practice Directions & Circulars
"""

NCLAT_DELHI_RULES = {

    # =========================================================================
    # LANGUAGE & FORMAT (Rule 20)
    # =========================================================================
    "language_english": {
        "id": "LANG_ENGLISH",
        "description": "Every petition/application must be in English. If in another Indian language, must be accompanied by certified English translation (Rule 20(1))",
        "check_type": "language",
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 20(1)",
    },
    "paper_book_format": {
        "id": "PAPER_BOOK",
        "description": "Documents must be stitched together in paper book form, on legal green sheets, single-sided printing only",
        "check_type": "manual",
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 20(2); NCLT Practice",
    },
    "double_spacing": {
        "id": "DOUBLE_SPACING",
        "description": "Must be typed/printed in DOUBLE SPACING on one side only of standard petition paper (Rule 20(2))",
        "check_type": "line_spacing",
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 20(2)",
    },
    "margins": {
        "id": "MARGINS",
        "description": "Inner/top margin approx 4cm width, right margin 2.5cm, left margin 5cm (Rule 20(2))",
        "check_type": "margin_check",
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 20(2)",
    },
    "font_size": {
        "id": "FONT_SIZE",
        "description": "Text must be fairly and legibly typewritten or printed. Standard practice: 12-14pt font for body text",
        "check_type": "font_size_check",
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 20(2); NCLT Practice",
    },
    "pagination": {
        "id": "PAGINATION",
        "description": "Document must be duly paginated — all pages must be numbered sequentially (Rule 20). Common defect: 'all pages numbering missed'",
        "check_type": "page_numbers",
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 20; NCLT Defect List",
    },
    "single_sided": {
        "id": "SINGLE_SIDED",
        "description": "Printing must be on ONE side only of the paper (Rule 20(2))",
        "check_type": "manual",
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 20(2)",
    },

    # =========================================================================
    # CAUSE TITLE & HEADING (Rule 20(3))
    # =========================================================================
    "cause_title_tribunal": {
        "id": "CAUSE_TITLE",
        "description": "Heading must state 'Before the National Company Law Tribunal' or 'In the National Company Law Tribunal' (Rule 20(3))",
        "required_phrases": [
            "NATIONAL COMPANY LAW TRIBUNAL",
            "NCLT",
        ],
        "min_matches": 1,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 20(3); Form NCLT.1",
    },
    "bench_info": {
        "id": "BENCH_INFO",
        "description": "Must specify the relevant Bench (e.g., 'MUMBAI BENCH', 'PRINCIPAL BENCH, NEW DELHI') (Rule 20(3))",
        "required_phrases": [
            "BENCH",
            "MUMBAI",
            "DELHI",
            "CHENNAI",
            "KOLKATA",
            "AHMEDABAD",
            "ALLAHABAD",
            "BENGALURU",
            "CHANDIGARH",
            "CUTTACK",
            "GUWAHATI",
            "HYDERABAD",
            "JAIPUR",
            "KOCHI",
            "PRINCIPAL BENCH",
            "AMRAVATI",
            "INDORE",
        ],
        "min_matches": 1,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 20(3)",
    },
    "case_number_format": {
        "id": "CASE_NUMBER",
        "description": "Must have proper case number format: CP (IB) No., IA No., CA No., TCP No., etc.",
        "patterns": [
            r"(?:CP|C\.P\.)\s*\(?IB\)?\s*(?:No\.?|NO\.?)\s*\d+",
            r"(?:I\.?A\.?|IA)\s*(?:\(.*?\))?\s*(?:No\.?|NO\.?)\s*\d+",
            r"(?:CA|C\.A\.)\s*(?:\(.*?\))?\s*(?:No\.?|NO\.?)\s*\d+",
            r"(?:TCP|T\.C\.P\.)\s*(?:No\.?|NO\.?)\s*\d+",
            r"Company\s+(?:Petition|Application)",
        ],
        "severity": "high",
        "source": "NCLT Rules 2016",
    },
    "provision_of_law": {
        "id": "PROVISION_OF_LAW",
        "description": "Relevant legal provision must IMMEDIATELY follow the cause title — e.g., 'Under Section 7 of IBC, 2016' (Rule 20(3))",
        "patterns": [
            r"[Ss]ection\s+\d+",
            r"[Rr]ule\s+\d+",
            r"[Rr]egulation\s+\d+",
            r"under\s+(?:the\s+)?(?:Companies\s+Act|Insolvency|IBC|Competition\s+Act)",
        ],
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 20(3)",
    },

    # =========================================================================
    # MEMO OF PARTIES (Rule 20(4)-(8))
    # =========================================================================
    "memo_of_parties": {
        "id": "MEMO_OF_PARTIES",
        "description": "Must clearly identify all parties with full names, parentage, age, description, addresses and representative capacity (Rule 20(4)-(5))",
        "required_phrases": [
            "APPELLANT",
            "RESPONDENT",
            "APPLICANT",
            "PETITIONER",
            "FINANCIAL CREDITOR",
            "OPERATIONAL CREDITOR",
            "CORPORATE DEBTOR",
            "RESOLUTION PROFESSIONAL",
        ],
        "min_matches": 2,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 20(4)-(8); NCLT Defect List",
    },
    "versus_separator": {
        "id": "VERSUS",
        "description": "Must have 'VERSUS' or 'V/s' separating parties in cause title",
        "required_phrases": ["VERSUS", "V/S", "VS."],
        "severity": "medium",
        "source": "NCLT Rules 2016, Form NCLT.1",
    },

    # =========================================================================
    # ADDRESS FOR SERVICE (Rule 21)
    # =========================================================================
    "party_address": {
        "id": "PARTY_ADDRESS",
        "description": "Address must include: road/street, municipal division, door number, PIN, fax/mobile, and valid EMAIL address (Rule 21)",
        "required_phrases": [
            "ADDRESS",
            "PIN",
            "ROAD",
            "STREET",
            "VILLAGE",
            "TOWN",
            "CITY",
            "DISTRICT",
            "STATE",
            "EMAIL",
            "MOBILE",
        ],
        "min_matches": 2,
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 21",
    },
    "party_email": {
        "id": "PARTY_EMAIL",
        "description": "Valid email address of parties must be provided for service of notices (Rule 21). Common defect: 'email address missing'",
        "patterns": [
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        ],
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 21; Rule 38",
    },

    # =========================================================================
    # INITIALLING ALTERATIONS (Rule 22)
    # =========================================================================
    "corrections_initialled": {
        "id": "CORRECTIONS",
        "description": "Every interlineation, erasure, correction or deletion must be initialled by the party or authorised representative (Rule 22)",
        "check_type": "manual",
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 22",
    },

    # =========================================================================
    # INDEX (Rule 23(3))
    # =========================================================================
    "index_with_details": {
        "id": "INDEX",
        "description": "Must be accompanied by INDEX containing document details, page numbers, and fee amount (Rule 23(3)). Common defect: 'index numbering missed'",
        "required_phrases": ["INDEX", "TABLE OF CONTENTS", "PARTICULARS", "PAGE NO", "S. NO", "S.NO", "SR. NO"],
        "min_matches": 2,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 23(3); NCLT Defect List",
    },
    "index_signed": {
        "id": "INDEX_SIGNED",
        "description": "Index must be signed by advocate/authorised representative. Common defect: 'counsel sign missed in index'",
        "check_type": "manual",
        "severity": "medium",
        "source": "NCLT Practice; NCLT Defect List",
    },

    # =========================================================================
    # PRESENTATION IN TRIPLICATE (Rule 23(1))
    # =========================================================================
    "triplicate_filing": {
        "id": "TRIPLICATE",
        "description": "Every petition/appeal must be presented in TRIPLICATE + one copy for each opposite party (Rule 23(1), Rule 24)",
        "check_type": "manual",
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 23(1), Rule 24",
    },

    # =========================================================================
    # SIGNATURE ON ALL PAGES (NCLT Practice + Defect List)
    # =========================================================================
    "all_pages_signed": {
        "id": "ALL_PAGES_SIGNED",
        "description": "ALL pages of pleadings must be signed by advocate/party — NCLT mandates this unlike other courts. Common defects: 'counsel sign missed in index', 'petition every page sign missed'",
        "check_type": "manual",
        "severity": "high",
        "source": "NCLT Practice; ncltadvocates.com; NCLT Defect List",
    },

    # =========================================================================
    # FORM NUMBERS (Rule 34)
    # =========================================================================
    "form_nclt1": {
        "id": "FORM_NCLT1",
        "description": "Petition/application must be filed in Form NCLT.1 with attachments, accompanied by Form NCLT.2 (Notice of Admission) (Rule 34(3))",
        "required_phrases": [
            "FORM",
            "NCLT",
            "PETITION",
            "APPLICATION",
        ],
        "min_matches": 2,
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 34(3)",
    },

    # =========================================================================
    # VERIFICATION AFFIDAVIT (Rule 34(4), Rules 125-130)
    # =========================================================================
    "verification_affidavit": {
        "id": "VERIFICATION_AFFIDAVIT",
        "description": "Every petition/application including IA must be VERIFIED by affidavit in Form NCLT.6 (Rule 34(4))",
        "required_phrases": [
            "AFFIDAVIT",
            "VERIFICATION",
            "VERIFIED",
            "SOLEMNLY AFFIRM",
            "AFFIRM AND STATE",
            "DEPONENT",
        ],
        "min_matches": 2,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 34(4); Form NCLT.6",
    },
    "affidavit_knowledge_clause": {
        "id": "AFFIDAVIT_KNOWLEDGE",
        "description": "Verification must segregate paragraphs into 'true to my knowledge' vs 'based on information received and believed to be true' (Form NCLT.6 / CPC Order XIX Rule 3)",
        "required_phrases": [
            "TRUE TO MY KNOWLEDGE",
            "TRUE TO THE KNOWLEDGE",
            "INFORMATION RECEIVED",
            "BELIEVED TO BE TRUE",
            "INFORMATION AND BELIEF",
            "BEST OF MY KNOWLEDGE",
        ],
        "min_matches": 2,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 126; Form NCLT.6; CPC Order XIX Rule 3",
        "applies_to": ["affidavit"],
    },
    "deponent_details": {
        "id": "DEPONENT_DETAILS",
        "description": "Affidavit must state deponent's full name, father's/husband's name, age, occupation, and residential address (Rule 125-126)",
        "required_phrases": [
            "SON OF",
            "DAUGHTER OF",
            "WIFE OF",
            "S/O",
            "D/O",
            "W/O",
            "AGED",
            "YEARS",
            "OCCUPATION",
            "RESIDING AT",
            "RESIDENT OF",
        ],
        "min_matches": 2,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 125-126; CPC Order XIX",
        "applies_to": ["affidavit"],
    },
    "affidavit_oath": {
        "id": "AFFIDAVIT_OATH",
        "description": "Affidavit must contain proper oath/affirmation language: 'I solemnly affirm and state' or 'I make oath and say' (Rule 127)",
        "required_phrases": [
            "SOLEMNLY AFFIRM",
            "AFFIRM AND STATE",
            "MAKE OATH",
            "OATH AND SAY",
            "SWORN",
            "AFFIRMED",
        ],
        "min_matches": 1,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 127; Form NCLT.6",
        "applies_to": ["affidavit"],
    },
    "affidavit_place_date": {
        "id": "AFFIDAVIT_PLACE_DATE",
        "description": "Affidavit must state place and date of execution (e.g., 'Verified at Mumbai on this 15th day of March 2026')",
        "required_phrases": [
            "VERIFIED AT",
            "SWORN AT",
            "AFFIRMED AT",
            "PLACE:",
            "DATE:",
            "DAY OF",
        ],
        "min_matches": 1,
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 127; CPC Order XIX Rule 3",
        "applies_to": ["affidavit"],
    },
    "affidavit_stamp_paper": {
        "id": "STAMP_PAPER",
        "description": "Verifying affidavit must be on Rs.20 Non-Judicial stamp paper",
        "required_phrases": [
            "STAMP PAPER",
            "NON-JUDICIAL",
            "NON JUDICIAL",
            "STAMP",
        ],
        "min_matches": 1,
        "severity": "medium",
        "source": "NCLT Practice; ncltadvocates.com",
    },
    "affidavit_notarization": {
        "id": "NOTARIZATION",
        "description": "Affidavit must be notarized — sworn/affirmed before advocate or notary who must affix official seal (Rule 127). Common defect: 'notarization overlooked'",
        "required_phrases": [
            "NOTARY",
            "NOTARIZED",
            "NOTARIAL",
            "OATH COMMISSIONER",
            "BEFORE ME",
        ],
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 127; NCLT Defect List",
        "applies_to": ["affidavit"],
    },
    "affidavit_separate_forms": {
        "id": "SEPARATE_AFFIDAVITS",
        "description": "Form NCLT.6 (verification affidavit) and Form NCLT.7 (evidentiary affidavit) must be SEPARATE documents — combining them is the most common procedural error",
        "check_type": "manual",
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 34(4), Rule 39; Forms NCLT.6 & NCLT.7",
        "applies_to": ["affidavit"],
    },

    # =========================================================================
    # ANNEXURES & DOCUMENTS (Rule 130)
    # =========================================================================
    "annexure_format": {
        "id": "ANNEXURE_FORMAT",
        "description": "Annexures must be labeled 'Annexure [number]' and sequenced properly. Attester must endorse each (Rule 130)",
        "patterns": [
            r"ANNEXURE\s*[-_]?\s*[A-Z]?\s*[-_]?\s*\d+",
            r"EXHIBIT\s*[-_]?\s*[A-Z]?\s*[-_]?\s*\d+",
        ],
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 130; NCLT Defect List",
    },
    "true_copy_certification": {
        "id": "TRUE_COPY",
        "description": "Every page of annexures/documents must bear 'True Copy' certification signed by advocate. Common defect: 'true copy signature missing on every page of annexures'",
        "required_phrases": [
            "TRUE COPY",
            "CERTIFIED COPY",
            "CERTIFIED TRUE COPY",
        ],
        "min_matches": 1,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 23(2); NCLT Defect List",
    },
    "true_copy_per_page": {
        "id": "TRUE_COPY_PAGES",
        "description": "True copy certification must appear on EVERY page of annexures — not just first page. Common defect flagged by NCLT registry",
        "check_type": "true_copy_coverage",
        "severity": "high",
        "source": "NCLT Practice; NCLT Defect List",
    },

    # =========================================================================
    # ENDORSEMENT & SIGNATURE (Rule 44)
    # =========================================================================
    "signature_block": {
        "id": "SIGNATURE_BLOCK",
        "description": "Must have name and signature of advocate/authorised representative at foot of pleading. Every petition signed and verified by party (Rule 44)",
        "required_phrases": [
            "ADVOCATE",
            "COUNSEL",
            "AUTHORIZED REPRESENTATIVE",
            "AUTHORISED REPRESENTATIVE",
            "RESOLUTION PROFESSIONAL",
            "ENROLLMENT NO",
            "ENROLMENT NO",
        ],
        "min_matches": 1,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 44",
    },

    # =========================================================================
    # VAKALATNAMA / MEMO OF APPEARANCE (Form NCLT.12)
    # =========================================================================
    "vakalatnama": {
        "id": "VAKALATNAMA",
        "description": "Vakalatnama (Form NCLT.12) must be filed by Lawyer on Rs.20 stamp paper, notarized, with court stamp duty. CA/CS must file memorandum of appearance",
        "required_phrases": [
            "VAKALATNAMA",
            "VAKALAT",
            "POWER OF ATTORNEY",
            "MEMO OF APPEARANCE",
            "MEMORANDUM OF APPEARANCE",
            "FORM NCLT",
        ],
        "min_matches": 1,
        "severity": "medium",
        "source": "NCLT Rules 2016; Form NCLT.12",
    },

    # =========================================================================
    # BOARD RESOLUTION (for companies)
    # =========================================================================
    "board_resolution": {
        "id": "BOARD_RESOLUTION",
        "description": "If filed by/on behalf of a company, must include Board Resolution authorizing the signatory. Common defect: 'missing board resolution'",
        "required_phrases": [
            "BOARD RESOLUTION",
            "RESOLUTION",
            "AUTHORIZED",
            "AUTHORISED",
            "BOARD OF DIRECTORS",
        ],
        "min_matches": 1,
        "severity": "medium",
        "source": "NCLT Practice; NCLT Defect List",
    },

    # =========================================================================
    # PRAYER / RELIEF
    # =========================================================================
    "prayer_clause": {
        "id": "PRAYER",
        "description": "Must contain Prayer/Relief clause specifying exact reliefs sought from the Tribunal",
        "required_phrases": ["PRAYER", "RELIEF", "RELIEFS SOUGHT", "PRAYED", "HUMBLY PRAY"],
        "severity": "high",
        "source": "NCLT Rules 2016; Form NCLT.1",
        "applies_to": ["application", "petition", "appeal"],
    },

    # =========================================================================
    # SYNOPSIS & LIST OF DATES (required for appeals, good practice for petitions)
    # =========================================================================
    "synopsis": {
        "id": "SYNOPSIS",
        "description": "Must contain Synopsis and chronological list of dates/events (mandatory for appeals; NCLT Chennai Bench Circular mandates for all matters)",
        "required_phrases": [
            "SYNOPSIS",
            "CHRONOLOGICAL",
            "LIST OF DATES",
            "LIST OF EVENTS",
        ],
        "min_matches": 1,
        "severity": "medium",
        "source": "NCLT Practice; NCLT Chennai Bench Circular dt. 04.01.2023",
        "applies_to": ["appeal"],
    },

    # =========================================================================
    # DATE FORMAT (Rule 20(9))
    # =========================================================================
    "date_format": {
        "id": "DATE_FORMAT",
        "description": "When Saka or other calendar dates are used, corresponding Gregorian date must also be stated (Rule 20(9))",
        "patterns": [
            r"\d{1,2}[./]\d{1,2}[./]\d{4}",
            r"\d{1,2}(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\s*,?\s*\d{4}",
        ],
        "severity": "low",
        "source": "NCLT Rules 2016, Rule 20(9)",
    },

    # =========================================================================
    # COURT FEES (Rule 112, Schedule of Fees)
    # =========================================================================
    "court_fees": {
        "id": "COURT_FEES",
        "description": "Prescribed fee must be paid. IBC S.7/9/10: Rs.25,000. IA fee separate. Payment by DD in favour of 'PAO, MCA' (Rule 112)",
        "required_phrases": [
            "FEE",
            "FEES",
            "DEMAND DRAFT",
            "PAY ORDER",
            "COURT FEE",
            "STAMP",
        ],
        "min_matches": 1,
        "severity": "low",
        "source": "NCLT Rules 2016, Rule 112; Schedule of Fees",
    },

    # =========================================================================
    # PLEADINGS FORMAT (Rule 20(6))
    # =========================================================================
    "pleadings_numbered": {
        "id": "PLEADINGS_NUMBERED",
        "description": "Pleadings must be divided into consecutively numbered paragraphs; each paragraph containing a separate fact/allegation (Rule 20(6))",
        "check_type": "paragraph_numbering",
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 20(6)",
    },

    # =========================================================================
    # CORPORATE IDENTITY (for IBC matters)
    # =========================================================================
    "corporate_identity": {
        "id": "CORPORATE_IDENTITY",
        "description": "Must include CIN, company registration details, registered office. Party names must match CIN/MCA records. Common defect: 'incorrect party names'",
        "required_phrases": [
            "CIN",
            "CORPORATE IDENTITY",
            "REGISTRATION",
            "REGISTERED OFFICE",
            "INCORPORATED",
        ],
        "min_matches": 1,
        "severity": "medium",
        "source": "NCLT Practice; NCLT Defect List",
    },

    # =========================================================================
    # E-FILING REQUIREMENTS (Orders dt. 28.09.2023 and 22.12.2023)
    # =========================================================================
    "bookmarking": {
        "id": "BOOKMARKING",
        "description": "PDF must be BOOKMARKED with section names and page numbers — mandatory per NCLT Principal Bench Order. Common defect: 'bookmarking being mandatory'",
        "check_type": "bookmarks",
        "severity": "high",
        "source": "NCLT Principal Bench Order dt. 28.09.2023; NCLT Defect List",
    },
    "bookmark_matches_index": {
        "id": "BOOKMARK_INDEX_MATCH",
        "description": "PDF bookmarks must match index pagination. Common defect: 'bookmarking not matching index pagination'",
        "check_type": "bookmark_index_match",
        "severity": "medium",
        "source": "NCLT Principal Bench Order dt. 28.09.2023; NCLT Defect List",
    },
    "ocr_scanning": {
        "id": "OCR_SCANNING",
        "description": "Document must be OCR-scanned (searchable PDF) per NCLT Principal Bench Order dt. 28.09.2023",
        "check_type": "ocr_check",
        "severity": "high",
        "source": "NCLT Principal Bench Order dt. 28.09.2023",
    },
    "digital_signature": {
        "id": "DIGITAL_SIGNATURE",
        "description": "E-filed documents must be signed using Class III Digital Signature Certificate (DSC) or e-Sign via Aadhaar (mandatory from 01.01.2024)",
        "check_type": "manual",
        "severity": "high",
        "source": "NCLT Principal Bench Order dt. 28.09.2023 & 22.12.2023",
    },
    "efiling_pdf_format": {
        "id": "PDF_FORMAT",
        "description": "E-filing: documents must be in PDF/A format, max 50MB, scanned at 300 DPI",
        "check_type": "pdf_check",
        "severity": "medium",
        "source": "NCLT E-Filing Manual; NCLT Principal Bench Order dt. 22.12.2023",
    },

    # =========================================================================
    # ADVERTISEMENT (Rule 35)
    # =========================================================================
    "advertisement": {
        "id": "ADVERTISEMENT",
        "description": "When required, advertisement in Form NCLT-3A — once in vernacular newspaper and once in English newspaper, not less than 14 days before hearing (Rule 35)",
        "check_type": "manual",
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 35",
        "applies_to": ["petition"],
    },

    # =========================================================================
    # APPEAL FILING CHECKLIST (6 Critical Rules)
    # =========================================================================
    "appeal_draft_check": {
        "id": "APPEAL_DRAFT",
        "description": "Appeal must NOT be in draft state — final signed version required. Check for 'DRAFT' watermark or header/footer text",
        "check_type": "draft_check",
        "severity": "high",
        "source": "NCLT Filing Practice; Registry Rejection Checklist",
        "applies_to": ["appeal"],
    },
    "appeal_doc_upload": {
        "id": "APPEAL_DOC_UPLOAD",
        "description": "All required documents must be uploaded/attached: Appeal memo, Certified copy of impugned order, Vakalat, Affidavit, Annexures, Board Resolution (if company)",
        "check_type": "doc_upload_check",
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 23; NCLT Filing Practice",
        "applies_to": ["appeal"],
    },
    "appeal_pagination_no_duplicates": {
        "id": "APPEAL_PAGINATION",
        "description": "Pages must be sequentially numbered with NO duplicate page numbers. Double pagination causes registry rejection",
        "check_type": "pagination_duplicate_check",
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 20; NCLT Defect List",
        "applies_to": ["appeal"],
    },
    "appeal_annexure_sign_stamp": {
        "id": "ANNEXURE_SIGN_STAMP",
        "description": "Every Annexure page must have advocate's signature AND stamp/seal. Missing sign/stamp on annexures is a common rejection reason",
        "check_type": "annexure_sign_stamp_check",
        "severity": "high",
        "source": "NCLT Practice; NCLT Defect List",
        "applies_to": ["appeal"],
    },
    "appeal_true_copy_annexures": {
        "id": "ANNEXURE_TRUE_COPY",
        "description": "Every page of every Annexure must bear 'TRUE COPY' endorsement by advocate. Not just the first page — EVERY page",
        "check_type": "annexure_true_copy_check",
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 23(2); NCLT Defect List",
        "applies_to": ["appeal"],
    },
    "appeal_index_page_numbers": {
        "id": "INDEX_PAGE_NUMBERS",
        "description": "Index must contain page number entries for each document/section listed. Common defect: 'index page numbers missing or incorrect'",
        "check_type": "index_page_number_check",
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 23(3); NCLT Defect List",
        "applies_to": ["appeal"],
    },

    # =========================================================================
    # LIMITATION & DELAY (for appeals/late filings)
    # =========================================================================
    "delay_condonation": {
        "id": "DELAY_CONDONATION",
        "description": "If filed beyond limitation, must include delay condonation application specifying EXACT number of days of delay. Common defect: 'delay condonation not specifying exact days'",
        "check_type": "delay_check",
        "severity": "high",
        "source": "NCLT Practice; NCLT Defect List",
        "applies_to": ["appeal"],
    },

    # =========================================================================
    # CERTIFIED COPY OF IMPUGNED ORDER (for appeals)
    # =========================================================================
    "impugned_order_copy": {
        "id": "IMPUGNED_ORDER",
        "description": "For appeals: CERTIFIED COPY of impugned order is mandatory (not photocopy). Common defect: 'only photocopy attached, not certified copy'",
        "required_phrases": [
            "CERTIFIED COPY",
            "IMPUGNED ORDER",
            "IMPUGNED",
            "ORDER DATED",
            "ORDER DT",
        ],
        "min_matches": 2,
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 23(2); NCLT Practice",
        "applies_to": ["appeal"],
    },

    # =========================================================================
    # IA SEPARATE FILING
    # =========================================================================
    "ia_separate": {
        "id": "IA_SEPARATE",
        "description": "Interlocutory Applications must be filed SEPARATELY — not bundled with main pleading. IA uses Form NCLT.1 with Form NCLT.3 (Notice of Motion)",
        "check_type": "manual",
        "severity": "medium",
        "source": "NCLT Rules 2016, Rule 34(3)",
    },

    # =========================================================================
    # TRANSLATION OF DOCUMENTS (Rule 26-27)
    # =========================================================================
    "translation_non_english": {
        "id": "TRANSLATION",
        "description": "Non-English documents must be accompanied by certified English translation, agreed by parties or certified by Registrar-approved translator (Rule 26)",
        "check_type": "manual",
        "severity": "high",
        "source": "NCLT Rules 2016, Rule 26-27",
    },

    # =========================================================================
    # DEFECT CURING TIMELINE (Rule 28)
    # =========================================================================
    "defect_timeline": {
        "id": "DEFECT_CURE",
        "description": "Registry defects must be cured within 7 days of notice, failing which Registrar may pass appropriate orders (Rule 28(2))",
        "check_type": "manual",
        "severity": "low",
        "source": "NCLT Rules 2016, Rule 28(2)",
    },
}


def get_rules_for_document_type(doc_type=None):
    """Get applicable rules for a given document type."""
    if doc_type is None:
        return NCLAT_DELHI_RULES

    applicable = {}
    for key, rule in NCLAT_DELHI_RULES.items():
        applies_to = rule.get("applies_to")
        if applies_to is None or doc_type in applies_to:
            applicable[key] = rule

    return applicable
