"""
Extract POS grades from uploaded PDF (text or scanned).
Returns grades_by_section (only sections with at least one grade) and validation errors.
"""
import re
import io
import tempfile
import os


# Valid grades (as in generate_pos.html)
VALID_GRADES = {"1.00", "1.25", "1.50", "1.75", "2.00", "2.25", "2.50", "2.75", "3.00", "5.00", "5.0", "5"}
VALID_GRADE_STRINGS = ("1.00", "1.25", "1.50", "1.75", "2.00", "2.25", "2.50", "2.75", "3.00", "5.00")

# Normalize grade to stored form
def _normalize_grade(g):
    if not g:
        return None
    s = str(g).strip().replace(",", ".")
    if s in ("5", "5.0"):
        return "5.00"
    if s in VALID_GRADES:
        return "5.00" if s in ("5", "5.0") else s
    try:
        f = float(s)
        if f == 5.0:
            return "5.00"
        if 1.0 <= f <= 3.0:
            return "%.2f" % f
        if 4.0 <= f <= 5.0:
            return "5.00"
    except (ValueError, TypeError):
        pass
    return None


def _normalize_code_key(code):
    """Normalize a course code for matching (case/spacing-insensitive)."""
    return re.sub(r"\s+", " ", str(code or "").strip()).upper()


def _code_at_line_start(line, valid_codes_norm_map):
    """Return canonical course code if the line starts with one (course-code column), else None.

    This avoids matching prerequisite codes inside the same row (e.g. PHYS 102 row contains MATH 101 prereq).
    """
    if not line:
        return None
    s = " ".join(str(line).split()).strip()
    if not s:
        return None
    parts = s.split(" ")
    if len(parts) >= 2:
        cand2 = _normalize_code_key(parts[0] + " " + parts[1])
        if cand2 in valid_codes_norm_map:
            return valid_codes_norm_map[cand2]
    cand1 = _normalize_code_key(parts[0])
    return valid_codes_norm_map.get(cand1)


def _get_valid_course_codes():
    """Set of all valid course codes from curriculum and flexible subjects."""
    from pos_pdf import POS_CURRICULUM, FLEXIBLE_SUBJECTS
    codes = set()
    for _t1, _t2, _tu, _lec, _lab, rows in POS_CURRICULUM:
        for r in rows:
            codes.add(r[0])
    for row in FLEXIBLE_SUBJECTS:
        codes.add(row[0])
    return codes


def _year_sem_to_index():
    """Build (year, sem) -> section_index from POS_CURRICULUM titles."""
    from pos_pdf import POS_CURRICULUM
    # Normalize "FIRST YEAR" -> "1st Year", "FIRST SEMESTER" -> "First Semester"
    ord_to_abbr = {"FIRST": "1st", "SECOND": "2nd", "THIRD": "3rd", "FOURTH": "4th", "FIFTH": "5th",
                   "SIXTH": "6th", "SEVENTH": "7th", "EIGHTH": "8th", "NINTH": "9th", "TENTH": "10th"}
    sem_norm = {"FIRST SEMESTER": "First Semester", "SECOND SEMESTER": "Second Semester", "MIDTERM": "Midterm"}
    out = {}
    for i, (title1, title2, *_rest) in enumerate(POS_CURRICULUM):
        word = title1.split()[0] if title1 else ""
        y = (ord_to_abbr.get(word, word) + " Year") if word else title1
        s = sem_norm.get(title2.upper(), title2) if title2 else title2
        out[(y, s)] = i
    return out


# Patterns for year/semester in text (case-insensitive)
YEAR_PATTERNS = [
    (re.compile(r"\b(1st|first)\s*year\b", re.I), "1st Year"),
    (re.compile(r"\b(2nd|second)\s*year\b", re.I), "2nd Year"),
    (re.compile(r"\b(3rd|third)\s*year\b", re.I), "3rd Year"),
    (re.compile(r"\b(4th|fourth)\s*year\b", re.I), "4th Year"),
    (re.compile(r"\b(5th|fifth)\s*year\b", re.I), "5th Year"),
    (re.compile(r"\b(6th|sixth)\s*year\b", re.I), "6th Year"),
    (re.compile(r"\b(7th|seventh)\s*year\b", re.I), "7th Year"),
    (re.compile(r"\b(8th|eighth)\s*year\b", re.I), "8th Year"),
    (re.compile(r"\b(9th|ninth)\s*year\b", re.I), "9th Year"),
    (re.compile(r"\b(10th|tenth)\s*year\b", re.I), "10th Year"),
]
SEM_PATTERNS = [
    (re.compile(r"\bfirst\s*semester\b", re.I), "First Semester"),
    (re.compile(r"\bsecond\s*semester\b", re.I), "Second Semester"),
    (re.compile(r"\bmidterm\b", re.I), "Midterm"),
    (re.compile(r"\b1st\s*sem(?:ester)?\b", re.I), "First Semester"),
    (re.compile(r"\b2nd\s*sem(?:ester)?\b", re.I), "Second Semester"),
    (re.compile(r"\bfirst\s*sem(?:ester)?\b", re.I), "First Semester"),
    (re.compile(r"\bsecond\s*sem(?:ester)?\b", re.I), "Second Semester"),
]

# Grade pattern: 1.00, 2.50, 5.00, 5.0, 5 (flexible; comma as decimal)
GRADE_PATTERN = re.compile(r"\b([123]\.\d{1,2}|5\.?0?|5)\b")
GRADE_PATTERN_COMMA = re.compile(r"\b([123],\d{1,2}|5,?0?)\b")
# Strict: only X.XX or 5.00/5.0/5 (reject bare 1,2,3 so we don't match units/lec/lab like "3 3 0")
GRADE_ONLY_PATTERN = re.compile(r"^\s*([123]\.\d{2}|5\.?0?)\s*$")
GRADE_ONLY_PATTERN_COMMA = re.compile(r"^\s*([123],\d{2}|5,?0?)\s*$")


def _cell_is_grade_only(cell):
    """True if cell contains only a grade (no extra digits/text). Rejects '3 3 0', 'ECE 405', '4th Year Standing'."""
    if not cell or not isinstance(cell, str):
        return None
    s = cell.strip().replace(",", ".")
    if GRADE_ONLY_PATTERN.match(cell.strip()):
        return _normalize_grade(cell.strip())
    if GRADE_ONLY_PATTERN_COMMA.match(cell.strip()):
        return _normalize_grade(cell.strip().replace(",", "."))
    if re.match(r"^\s*([123]\.\d{2}|5\.?0?)\s*$", s):
        return _normalize_grade(s)
    return None


def _grade_at_end_of_line(line):
    """Return the rightmost normalized grade on the line (grade column).

    Avoids units/lec/lab like '3 3 0' because only grade-shaped numbers (1.xx–3.xx, 5/5.0/5.00) are matched.
    """
    line_norm = " ".join(line.split()).strip()
    if not line_norm:
        return None
    last_grade = None
    for pat in (GRADE_PATTERN, GRADE_PATTERN_COMMA):
        for m in pat.finditer(line_norm):
            g = m.group(1).replace(",", ".")
            norm = _normalize_grade(g)
            if norm and norm in VALID_GRADE_STRINGS:
                last_grade = norm
    return last_grade


def _extract_text_from_pdf(pdf_file, use_layout_false=False, progress_list=None):
    """Get raw text from PDF. Tries pdfplumber first; if empty/short, tries pypdf then OCR.
    If progress_list is a list of one int, updates it 10->15->25->30->65 for progress reporting."""
    raw = b""
    if hasattr(pdf_file, "read"):
        raw = pdf_file.read()
    else:
        raw = pdf_file
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    file_like = io.BytesIO(raw)
    if progress_list is not None:
        progress_list[0] = 10

    text = ""
    try:
        import pdfplumber
        with pdfplumber.open(file_like) as pdf:
            for page in pdf.pages:
                try:
                    t = page.extract_text(layout=False) if use_layout_false else page.extract_text()
                except TypeError:
                    t = page.extract_text()
                if t:
                    text += t + "\n"
    except Exception:
        pass
    if progress_list is not None:
        progress_list[0] = 15

    # Fallback: pypdf
    if len(text.strip()) < 300:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            parts = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
            if parts:
                text = "\n".join(parts)
        except Exception:
            pass
    if progress_list is not None:
        progress_list[0] = 25

    # OCR if still very little text
    if len(text.strip()) < 300:
        if progress_list is not None:
            progress_list[0] = 30
        page_images = _pdf_to_images(raw)
        if page_images:
            ocr_text = _run_ocr_on_images(page_images, progress_list=progress_list, progress_range=(30, 65))
            if ocr_text.strip():
                text += ocr_text
    if progress_list is not None and progress_list[0] < 65:
        progress_list[0] = 65

    return text


def _pdf_to_images(raw, _collect_errors=None):
    """Render PDF bytes to list of PIL Images. Tries temp file + PyMuPDF, then stream, then pdf2image.
    If _collect_errors is a list, append short error strings when a step fails (for diagnostics)."""
    errors = _collect_errors if _collect_errors is not None else []
    if not raw or len(raw) < 100:
        if errors is not None:
            errors.append("PDF too short or empty")
        return []
    images = []
    temp_path = None

    def try_fitz_open(doc_path=None, stream_data=None):
        doc = None
        try:
            if doc_path and os.path.exists(doc_path):
                doc = fitz.open(doc_path)
            elif stream_data is not None:
                doc = fitz.open(stream=stream_data, filetype="pdf")
        except Exception as e:
            if errors is not None:
                errors.append("fitz open: %s" % (str(e)[:120],))
            return None
        if doc is not None and getattr(doc, "is_encrypted", False):
            try:
                if doc.authenticate("") == 0:
                    doc.close()
                    doc = None
            except Exception:
                doc.close()
                doc = None
        return doc

    def render_fitz_pages(doc):
        out = []
        from PIL import Image
        for page_num in range(len(doc)):
            try:
                page = doc[page_num]
                mat = fitz.Matrix(2.0, 2.0)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_bytes = pix.tobytes("png")
                out.append(Image.open(io.BytesIO(img_bytes)))
            except Exception as e:
                if errors is not None and not out:
                    errors.append("fitz render: %s" % (str(e)[:80],))
                continue
        return out

    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        fitz = None
        if errors is not None:
            errors.append("fitz import: %s" % str(e))

    # 1) Temp file + fitz (most reliable on Windows)
    try:
        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        try:
            os.write(fd, raw)
            os.close(fd)
            fd = None
            if fitz is not None:
                doc = try_fitz_open(doc_path=temp_path)
                if doc is not None:
                    try:
                        images = render_fitz_pages(doc)
                    finally:
                        doc.close()
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
    except Exception as e:
        if errors is not None:
            errors.append("temp file: %s" % (str(e)[:80],))

    # 2) fitz from stream (bytes and bytearray)
    if not images and fitz is not None:
        for stream_val in (raw, bytearray(raw)):
            if images:
                break
            try:
                doc = try_fitz_open(stream_data=stream_val)
                if doc is not None:
                    try:
                        images = render_fitz_pages(doc)
                    finally:
                        doc.close()
            except Exception:
                pass

    # 3) pdf2image from bytes
    if not images:
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(raw)
        except Exception as e:
            if errors is not None:
                errors.append("pdf2image bytes: %s" % (str(e)[:120],))

    # 4) pdf2image from temp file
    if not images and temp_path and os.path.exists(temp_path):
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(temp_path)
        except Exception as e:
            if errors is not None:
                errors.append("pdf2image path: %s" % (str(e)[:120],))

    if temp_path and os.path.exists(temp_path):
        try:
            os.unlink(temp_path)
        except Exception:
            pass

    return images if images else []


def _run_ocr_on_images(images, progress_list=None, progress_range=(30, 65)):
    """Run OCR on list of PIL Images. If progress_list is a list of one int, updates it across progress_range per page."""
    if not images:
        return ""
    tesseract_text = ""
    easyocr_text = ""
    n = len(images)
    lo, hi = progress_range

    # 1) Tesseract
    try:
        import pytesseract
        parts = []
        for i, img in enumerate(images):
            t = pytesseract.image_to_string(img)
            if t and t.strip():
                parts.append(t.strip())
            if progress_list is not None and n:
                progress_list[0] = lo + (hi - lo) * (i + 1) // n
        tesseract_text = "\n".join(parts) if parts else ""
    except Exception:
        pass

    # 2) EasyOCR (if Tesseract didn't give enough)
    if not tesseract_text.strip():
        try:
            import easyocr
            import numpy as np
            reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            parts = []
            for i, img in enumerate(images):
                arr = np.array(img)
                results = reader.readtext(arr)
                line_texts = [item[1] for item in results if item[1].strip()]
                if line_texts:
                    parts.append(" ".join(line_texts))
                if progress_list is not None and n:
                    progress_list[0] = lo + (hi - lo) * (i + 1) // n
            easyocr_text = "\n".join(parts) if parts else ""
        except Exception:
            pass

    if progress_list is not None:
        progress_list[0] = hi
    if tesseract_text and easyocr_text:
        return tesseract_text if len(tesseract_text) >= len(easyocr_text) else easyocr_text
    return tesseract_text or easyocr_text


def _extract_tables_from_pdf(pdf_file):
    """Extract tables from each page. Returns list of lists of rows (each row is list of cell strings)."""
    raw = b""
    if hasattr(pdf_file, "read"):
        raw = pdf_file.read()
    else:
        raw = pdf_file
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    file_like = io.BytesIO(raw)
    all_tables = []
    try:
        import pdfplumber
        with pdfplumber.open(file_like) as pdf:
            for page in pdf.pages:
                tables = page.find_tables()
                for t in tables:
                    data = t.extract()
                    if data:
                        all_tables.append(data)
    except Exception:
        pass
    return all_tables


def _parse_year_semester(line, current_year, current_sem):
    """Update current_year, current_sem from line if matched."""
    for pat, canonical in YEAR_PATTERNS:
        if pat.search(line):
            current_year = canonical
            break
    for pat, canonical in SEM_PATTERNS:
        if pat.search(line):
            current_sem = canonical
            break
    return current_year, current_sem


def _find_section_in_cell_text(cell_text, year_sem_to_index):
    """If cell text contains year+semester, return section index else None."""
    if not cell_text:
        return None
    current_year, current_sem = None, None
    current_year, current_sem = _parse_year_semester(str(cell_text), current_year, current_sem)
    if current_year and current_sem:
        return year_sem_to_index.get((current_year, current_sem))
    return None


def _extract_from_tables(pdf_file, valid_codes, year_sem_to_index):
    """Extract (section_index, code, grade) from PDF tables. POS layout: often Code column + Grade column.
    Detects section headers, optional 'Grade' column header, and pairs code cell with grade cell per row."""
    by_section = {}
    errors = []
    tables = _extract_tables_from_pdf(pdf_file)
    current_si = 0
    for table in tables:
        if not table:
            continue
        grade_col_idx = None  # detect column that contains grades (header "Grade" or last col)
        for row_idx, row in enumerate(table):
            if not row:
                continue
            cells = [str(c).strip() if c is not None else "" for c in row]
            if row_idx < 4:
                for ci, cell in enumerate(cells):
                    si = _find_section_in_cell_text(cell, year_sem_to_index)
                    if si is not None:
                        current_si = si
                        break
                if row_idx == 0 or row_idx == 1:
                    for ci, cell in enumerate(cells):
                        if cell and cell.lower().strip() == "grade":
                            grade_col_idx = ci
                            break
            if grade_col_idx is not None and grade_col_idx < len(cells):
                g = _cell_is_grade_only(cells[grade_col_idx])
                if g and g in VALID_GRADE_STRINGS:
                    found_code = None
                    for ci, cell in enumerate(cells):
                        if ci == grade_col_idx:
                            continue
                        cell_norm = " ".join(cell.split()) if cell else ""
                        for code in valid_codes:
                            if code in cell_norm or cell_norm == code or cell == code:
                                found_code = code
                                break
                        if found_code:
                            break
                    if found_code:
                        by_section.setdefault(current_si, {})[found_code] = g
                        continue
            found_code = None
            grade_norm = None
            for cell in cells:
                if not cell:
                    continue
                cell_norm = " ".join(cell.split())
                for code in valid_codes:
                    if code in cell_norm or cell_norm == code or cell == code:
                        found_code = code
                        break
                if found_code:
                    break
            for cell in cells:
                g = _cell_is_grade_only(cell)
                if g and g in VALID_GRADE_STRINGS:
                    grade_norm = g
                    break
            if found_code and grade_norm:
                by_section.setdefault(current_si, {})[found_code] = grade_norm
    return by_section, errors


# Proximity: chars after a course code to look for grade (POS layout often has code and grade same row or nearby)
GRADE_PROXIMITY_CHARS = 400


def _extract_grades_from_blob(blob, valid_codes):
    """Find (code, grade) pairs in blob: for each valid code, look for a grade within GRADE_PROXIMITY_CHARS after it.
    Returns dict {code: grade}. Only exact valid_codes and grades in VALID_GRADE_STRINGS."""
    out = {}
    blob = " " + blob.replace("\n", " ").replace("\r", " ") + " "
    for code in valid_codes:
        for m in re.finditer(r"\b" + re.escape(code) + r"\b", blob):
            segment = blob[m.start() : m.start() + GRADE_PROXIMITY_CHARS]
            # Use rightmost grade in segment (grade column); avoids units/lec/lab like "3 3 0"
            grade_norm = None
            for pat in (GRADE_PATTERN, GRADE_PATTERN_COMMA):
                for gm in pat.finditer(segment):
                    g = gm.group(1).replace(",", ".")
                    n = _normalize_grade(g)
                    if n and n in VALID_GRADE_STRINGS:
                        grade_norm = n
            if grade_norm and grade_norm in VALID_GRADE_STRINGS:
                out[code] = grade_norm
            break  # one grade per code in this blob
    return out


def _extract_grades_from_block_lines(block_text, valid_codes):
    """Within a block, find (code, grade) by scanning line-by-line: code on line, grade on same line or next 3 lines.
    POS PDFs often have one row per course with grade in same or next cell/line."""
    out = {}
    lines = [ln.strip() for ln in block_text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        line_norm = " ".join(line.split())
        found_code = None
        for code in valid_codes:
            if re.search(r"\b" + re.escape(code) + r"\b", line_norm):
                found_code = code
                break
        if not found_code:
            continue
        grade_norm = _grade_at_end_of_line(line)
        if not grade_norm:
            for j in range(i + 1, min(i + 4, len(lines))):
                grade_norm = _grade_at_end_of_line(lines[j])
                if grade_norm:
                    break
        if grade_norm and grade_norm in VALID_GRADE_STRINGS:
            out[found_code] = grade_norm
    return out


def _build_section_blocks(text, year_sem_to_index):
    """Split text into blocks by section headers. Returns [(section_index, block_text), ...].
    Each block is the text from that section header up to (not including) the next section header."""
    blocks = []
    current_year = None
    current_sem = None
    current_si = None
    current_lines = []
    pos = 0
    for line in text.splitlines():
        line_stripped = line.strip()
        prev_year = current_year
        current_year, current_sem = _parse_year_semester(line, current_year, current_sem)
        if current_year and current_year != prev_year:
            current_sem = None
        si = year_sem_to_index.get((current_year, current_sem)) if (current_year and current_sem) else None
        if si is not None:
            if current_si is None:
                current_si = si
            elif si != current_si:
                if current_lines:
                    blocks.append((current_si, "\n".join(current_lines)))
                current_si = si
                current_lines = []
        if line_stripped:
            current_lines.append(line_stripped)
        pos += len(line) + 1
    if current_lines and current_si is not None:
        blocks.append((current_si, "\n".join(current_lines)))
    if not blocks:
        blocks = [(0, text)]  # no headers found: treat whole text as section 0
    return blocks


def _section_boundaries_from_text(text, year_sem_to_index):
    """Scan text line-by-line; return list of (char_offset, section_index) at each section header.
    When year level changes, semester is reset so each block is (year, semester) e.g. 1st Year First Sem, then 1st Year Second Sem."""
    boundaries = [(0, 0)]  # start at section 0
    current_year = None
    current_sem = None
    current_si = 0
    pos = 0
    last_seen_si = None
    for line in text.splitlines():
        line_stripped = line.strip()
        if line_stripped:
            prev_year = current_year
            current_year, current_sem = _parse_year_semester(line_stripped, current_year, current_sem)
            # New year level: reset semester so we don't reuse previous semester (e.g. 2nd Year + "First Semester" of 1st year)
            if current_year and current_year != prev_year:
                current_sem = None
            if current_year and current_sem:
                si = year_sem_to_index.get((current_year, current_sem))
                if si is not None:
                    current_si = si
                    if last_seen_si is None or current_si != last_seen_si:
                        boundaries.append((pos, current_si))
                        last_seen_si = current_si
        pos += len(line) + 1  # +1 for newline
    return boundaries


def _reassign_sections_by_position(text, by_section, year_sem_to_index):
    """Reassign grades from section 0 to correct section by position in text (section headers)."""
    section_zero = by_section.get(0, {})
    if not section_zero:
        return by_section
    boundaries = _section_boundaries_from_text(text, year_sem_to_index)
    if len(boundaries) <= 1:
        return by_section
    # Build new by_section: copy non-zero sections, reassign section 0 entries
    out = {k: dict(v) for k, v in by_section.items() if k != 0}
    for code, grade in section_zero.items():
        m = re.search(r"\b" + re.escape(code) + r"\b", text)
        if not m:
            out.setdefault(0, {})[code] = grade
            continue
        pos = m.start()
        si = 0
        for (bound_pos, bound_si) in boundaries:
            if bound_pos <= pos:
                si = bound_si
            else:
                break
        out.setdefault(si, {})[code] = grade
    return out


def _extract_grades_fulltext_loose(text, valid_codes, year_sem_to_index):
    """Loose fallback extractor: scan full text line-by-line, match any occurrence of a valid code in the line,
    and take the grade only if it appears at the end of the line (grade column).

    Used only when stricter extractors find no grades at all.
    """
    by_section = {}
    current_year = None
    current_sem = None
    current_si = 0
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        prev_year = current_year
        current_year, current_sem = _parse_year_semester(line, current_year, current_sem)
        if current_year and current_year != prev_year:
            current_sem = None
        if current_year and current_sem:
            si = year_sem_to_index.get((current_year, current_sem))
            if si is not None:
                current_si = si
        line_norm = " ".join(line.split())
        found_code = None
        for code in valid_codes:
            if re.search(r"\b" + re.escape(code) + r"\b", line_norm):
                found_code = code
                break
        grade_norm = _grade_at_end_of_line(line_norm)
        if found_code and not grade_norm:
            for j in range(i + 1, min(i + 4, len(lines))):
                grade_norm = _grade_at_end_of_line(lines[j])
                if grade_norm:
                    break
        if found_code and grade_norm and grade_norm in VALID_GRADE_STRINGS:
            by_section.setdefault(current_si, {})[found_code] = grade_norm
    return by_section


def extract_pos_from_pdf(pdf_file, progress_list=None):
    """
    Extract grades from PDF. Returns (grades_by_section, errors).
    If progress_list is a list of one int (0-100), it is updated at each stage for real progress.
    """
    def prog(pct):
        if progress_list is not None:
            progress_list[0] = min(100, max(0, pct))

    prog(5)
    # Read once
    raw = pdf_file.read() if hasattr(pdf_file, "read") else pdf_file
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    file_like = io.BytesIO(raw)

    valid_codes = _get_valid_course_codes()
    valid_codes_norm_map = {_normalize_code_key(c): c for c in valid_codes}
    year_sem_to_index = _year_sem_to_index()
    errors = []
    by_section = {}

    # 1) Get text (pdfplumber, pypdf, then OCR if needed; progress 10->65 inside _extract_text_from_pdf)
    text = ""
    for use_layout in (True, False):
        file_like.seek(0)
        text = _extract_text_from_pdf(file_like, use_layout_false=use_layout, progress_list=progress_list)
        if text and len(text.strip()) >= 100:
            break
    prog(70)
    if not text.strip():
        # Diagnose: get page images and capture why rendering might fail
        diag = []
        page_images = _pdf_to_images(raw, _collect_errors=diag)
        n_pages = len(page_images)
        # If fitz (PyMuPDF) is missing, give step-by-step fix
        if diag and "fitz import" in (diag[0] if diag else ""):
            msg = (
                "PDF rendering failed: PyMuPDF is not installed. "
                "Fix: 1) Open a terminal in your project folder. "
                "2) Activate the same environment you use for runserver (e.g. venv\\Scripts\\activate). "
                "3) Run: pip install pymupdf. "
                "4) Restart the Django server. "
                "To verify: python -c \"import fitz; print('OK')\" should print OK."
            )
        elif n_pages == 0:
            detail = (" " + diag[0]) if diag else ""
            msg = (
                "Could not extract any text from the PDF and could not render it as images.%s "
                "Try: open the PDF in a viewer (e.g. Chrome or Edge), use Print → Save as PDF, then upload that copy."
            ) % detail
        else:
            msg = (
                "Could not extract any text from the PDF (%d page(s) rendered). "
                "OCR (Tesseract + EasyOCR) returned no text. "
                "Fix: run your app with start.ps1 (it installs easyocr), or in the same environment run: pip install easyocr. "
                "Then restart the server and try again. First EasyOCR run may take a minute to download models."
            ) % n_pages
        return {}, [msg]

    # 2) Table extraction first (POS PDFs often have clear table: code column + grade column)
    file_like.seek(0)
    table_by_section, _ = _extract_from_tables(file_like, valid_codes, year_sem_to_index)
    for si, section_dict in table_by_section.items():
        for code, grade in section_dict.items():
            if code in valid_codes and grade in VALID_GRADE_STRINGS:
                by_section.setdefault(si, {})[code] = grade
    prog(72)

    # 3) Section blocks: blob + line-by-line per block (catches text/OCR layout)
    blocks = _build_section_blocks(text, year_sem_to_index)
    for si, block_text in blocks:
        for code, grade in _extract_grades_from_block_lines(block_text, valid_codes).items():
            if code in valid_codes and grade in VALID_GRADE_STRINGS:
                by_section.setdefault(si, {})[code] = grade
    prog(80)

    # 4) Full-text line-by-line fallback (merge in any we missed)
    if True:
        current_year = None
        current_sem = None
        current_si = 0
        lines = text.splitlines()
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            prev_year = current_year
            current_year, current_sem = _parse_year_semester(line, current_year, current_sem)
            if current_year and current_year != prev_year:
                current_sem = None
            if current_year and current_sem:
                si = year_sem_to_index.get((current_year, current_sem))
                if si is not None:
                    current_si = si
            line_norm = " ".join(line.split())
            found_code = None
            for code in valid_codes:
                if re.search(r"\b" + re.escape(code) + r"\b", line_norm):
                    found_code = code
                    break
            grade_norm = _grade_at_end_of_line(line_norm)
            if found_code and not grade_norm:
                for j in range(i + 1, min(i + 4, len(lines))):
                    grade_norm = _grade_at_end_of_line(lines[j])
                    if grade_norm:
                        break
            if found_code and grade_norm and grade_norm in VALID_GRADE_STRINGS:
                by_section.setdefault(current_si, {})[found_code] = grade_norm

    prog(95)
    # Build result, filtering to valid codes/grades
    def _build_result(sections_dict):
        out = {}
        for k, v in sections_dict.items():
            if not v:
                continue
            filtered = {
                code: grade
                for code, grade in v.items()
                if code in valid_codes and grade in VALID_GRADE_STRINGS
            }
            if filtered:
                out[str(k)] = filtered
        return out

    grades_by_section = _build_result(by_section)
    # If strict paths produced nothing usable, run a looser fallback extractor so we don't return empty
    if not grades_by_section:
        by_section = _extract_grades_fulltext_loose(text, valid_codes, year_sem_to_index)
        grades_by_section = _build_result(by_section)

    prog(100)
    return grades_by_section, errors
