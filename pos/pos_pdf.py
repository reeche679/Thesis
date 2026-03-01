"""
Generate POS (Program of Study) PDF in BSU BSCpE layout.
Design matches template: BSU logo, header with specified fonts, student info table, course tables, footer.
"""
import os
from io import BytesIO
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import legal
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
)

# Curriculum: (section_title_line1, section_title_line2, total_units, lec_total, lab_total, rows).
# Each row: (course_code, course_title, units, lec, lab, prereq, coreq)
POS_CURRICULUM = [
    ("FIRST YEAR", "FIRST SEMESTER", 24, 21, 9, [
        ("ENGG 101", "Introduction to Engineering", 1, 0, 3, "", ""),
        ("GEd 108", "Art Appreciation", 3, 3, 0, "", ""),
        ("GEd 104", "The Contemporary World", 3, 3, 0, "", ""),
        ("CpE 101", "Computer Programming", 1, 0, 3, "", ""),
        ("GEd 105", "Readings in Philippines History", 3, 3, 0, "", ""),
        ("MATH 101", "Differential Calculus", 4, 4, 0, "", ""),
        ("CHEM 101", "Chemistry for Engineers", 4, 3, 3, "", ""),
        ("PATHFit 1", "Movement Competency Training", 2, 2, 0, "", ""),
        ("NSTP 111", "National Service Training Program 1", 3, 3, 0, "", ""),
    ]),
    ("FIRST YEAR", "SECOND SEMESTER", 27, 24, 6, [
        ("ENGG 102", "Engineering Drawing", 1, 0, 3, "", ""),
        ("GEd 101", "Understanding the Self", 3, 3, 0, "", ""),
        ("GEd 102", "Mathematics in the Modern World", 3, 3, 0, "", ""),
        ("GEd 106", "Purposive Communication", 3, 3, 0, "", ""),
        ("GEd 109", "Science, Technology and Society", 3, 3, 0, "", ""),
        ("MATH 102", "Integral Calculus", 4, 4, 0, "MATH 101", ""),
        ("PHYS 102", "Physics for Engineers 1", 4, 3, 3, "MATH 101", "MATH 102"),
        ("PATHFit 2", "Exercise-based Fitness Activities", 2, 2, 0, "PATHFit 1", ""),
        ("NSTP 121", "National Service Training Program 2", 3, 3, 0, "NSTP 111", ""),
    ]),
    ("FIRST YEAR", "MIDTERM", 9, 8, 3, [
        ("GEd 103", "Life and Works of Rizal", 3, 3, 0, "", ""),
        ("GEd 107", "Ethics", 3, 3, 0, "", ""),
        ("PHYS 103", "Physics for Engineers 2", 3, 2, 3, "PHYS 102; MATH 102", ""),
    ]),
    ("SECOND YEAR", "FIRST SEMESTER", 27, 23, 12, [
        ("CpE 201", "CpE Discipline, Professional Practice and laws", 3, 3, 0, "", ""),
        ("CpE 202", "Discrete Mathematics", 3, 3, 0, "MATH 101", ""),
        ("CpE 203", "Programming Logic and Design", 2, 0, 6, "", ""),
        ("GEdE 101", "The Entrepreneurial Mind", 3, 3, 0, "", ""),
        ("EE 212", "Fundamentals of Electrical Circuits", 4, 3, 3, "PHY 103", ""),
        ("ENGG 103", "Computer-Aided Design", 1, 0, 3, "ENGG 102", ""),
        ("ENGG 104", "Engineering Economics", 3, 3, 0, "MATH 102", ""),
        ("AI 101", "Linear Algebra for AI", 3, 3, 0, "", ""),
        ("MATH 103", "Differential Equations", 3, 3, 0, "MATH 102", ""),
        ("PATHFit 3", "Choice of dance, sports, Martial Arts, Group exercise, Outdoor and Adventure Activities 1", 2, 2, 0, "PATHFit 1 & 2", ""),
    ]),
    ("SECOND YEAR", "SECOND SEMESTER", 23, 19, 12, [
        ("CpE 204", "Advance Engineering Mathematics for CpE", 3, 3, 0, "2nd Year Standing", ""),
        ("CpE 205", "Object-Oriented Programming", 2, 0, 6, "CpE 203", ""),
        ("CpEE 401", "Elective Course 1", 3, 2, 3, "2nd Year Standing", ""),
        ("ECE 405", "Electronic Circuits: Devices and Analysis", 4, 3, 3, "EE 212", ""),
        ("ENGG 114", "Basic Occupational Safety and Health", 3, 3, 0, "", ""),
        ("MATH 104", "Numerical Methods", 3, 3, 0, "Math 103", ""),
        ("AI 102", "Probability and Statistics for AI", 3, 3, 0, "MATH 102", ""),
        ("PATHFit 4", "Choice of Dance, Sports, Martial Arts, Group Exercise, Outdoor, and Adventure Activities 2", 2, 2, 0, "PATHFit 1 & 2", ""),
    ]),
    ("SECOND YEAR", "MIDTERM", 0, 0, 0, []),
    ("THIRD YEAR", "FIRST SEMESTER", 21, 16, 15, [
        ("CpE 302", "Logic Circuits and Design", 4, 3, 3, "ECE 405", ""),
        ("CpE 303", "Data Structures and Algorithms", 2, 0, 6, "CpE 205", ""),
        ("CpE 304", "Introduction to Networks, Data and Digital Communications", 3, 2, 3, "ECE 405", "CpE 302"),
        ("CpE 305", "Fundamentals of Mixed Signals and Sensors", 3, 3, 0, "ECE 405", ""),
        ("CpE 306", "Feedback and Control Systems", 3, 3, 0, "CpE 206; EE 212", ""),
        ("ENGG 116", "Methods of Research", 3, 3, 0, "AI 102, 3rd Year Standing", ""),
        ("CpEE 402", "Elective Course 2", 3, 2, 3, "CpEE 402", ""),
    ]),
    ("THIRD YEAR", "SECOND SEMESTER", 23, 17, 18, [
        ("CpE 308", "Microprocessors", 4, 3, 3, "CpE 302", ""),
        ("CpE 309", "Software Design", 4, 3, 3, "CpE 303", ""),
        ("CpE 310", "Digital Signal Processing", 4, 3, 3, "CpE 305; CpE 306", ""),
        ("CpE 311", "Computer Architecture and Organization", 4, 3, 3, "", "CpE 308"),
        ("CpE 312", "Connecting Networks and Security", 4, 3, 3, "CpE 304", ""),
        ("AI 103", "Machine Learning and Neural Networks", 3, 2, 3, "AI 101, AI 102, CpE 101", ""),
    ]),
    ("THIRD YEAR", "MIDTERM", 5, 3, 6, [
        ("CpE 313", "Introduction to HDL", 1, 0, 3, "CpE 203, CpE 302", ""),
        ("CpE 314", "Emerging Technologies in CpE", 3, 3, 0, "3rd Year Standing", ""),
        ("CpE 315", "Computer Engineering Drafting and Design", 1, 0, 3, "ECE 405", ""),
    ]),
    ("FOURTH YEAR", "FIRST SEMESTER", 18, 13, 15, [
        ("CpE 401", "Operating Systems", 3, 3, 0, "CpE 303", ""),
        ("CpE 402", "Embedded Systems", 4, 3, 3, "CpE 308", ""),
        ("CpE 403", "Seminars and Fieldtrips", 2, 0, 6, "4th Year Standing", ""),
        ("CpE 404", "CpE Practice and Design 1", 1, 0, 3, "ENGG 116, CpE 308", ""),
        ("ENGG 106", "Engineering Management", 2, 2, 0, "4th Year Standing", ""),
        ("ENGG 105", "Technopreneurship", 3, 3, 0, "4th Year Standing", ""),
        ("CpEE 403", "Elective Course 3", 3, 2, 3, "CpEE 403", ""),
    ]),
    ("FOURTH YEAR", "SECOND SEMESTER", 8, 0, 12, [
        ("ENGG 117", "OJT/Internship (320 hrs)", 4, 4, 0, "4th Year Standing", ""),
        ("CpE 405", "CpE Practice and Design 2", 2, 0, 6, "CpE 404", ""),
        ("CpE 406", "Comprehensive Examination for CpE", 2, 0, 6, "Graduating", ""),
    ]),
    # 5th–10th year: empty rows so user can add grades; preview/PDF show these sections
    ("FIFTH YEAR", "FIRST SEMESTER", 0, 0, 0, []),
    ("FIFTH YEAR", "SECOND SEMESTER", 0, 0, 0, []),
    ("FIFTH YEAR", "MIDTERM", 0, 0, 0, []),
    ("SIXTH YEAR", "FIRST SEMESTER", 0, 0, 0, []),
    ("SIXTH YEAR", "SECOND SEMESTER", 0, 0, 0, []),
    ("SIXTH YEAR", "MIDTERM", 0, 0, 0, []),
    ("SEVENTH YEAR", "FIRST SEMESTER", 0, 0, 0, []),
    ("SEVENTH YEAR", "SECOND SEMESTER", 0, 0, 0, []),
    ("SEVENTH YEAR", "MIDTERM", 0, 0, 0, []),
    ("EIGHTH YEAR", "FIRST SEMESTER", 0, 0, 0, []),
    ("EIGHTH YEAR", "SECOND SEMESTER", 0, 0, 0, []),
    ("EIGHTH YEAR", "MIDTERM", 0, 0, 0, []),
    ("NINTH YEAR", "FIRST SEMESTER", 0, 0, 0, []),
    ("NINTH YEAR", "SECOND SEMESTER", 0, 0, 0, []),
    ("NINTH YEAR", "MIDTERM", 0, 0, 0, []),
    ("TENTH YEAR", "FIRST SEMESTER", 0, 0, 0, []),
    ("TENTH YEAR", "SECOND SEMESTER", 0, 0, 0, []),
    ("TENTH YEAR", "MIDTERM", 0, 0, 0, []),
]

# Flexible subjects: can be offered in additional semesters. List of (section_index, dept_note).
# dept_note shown next to course when offered that sem (e.g. "EE Dept").
# GEdE 101: 2nd Yr 1st or 2nd Yr 2nd (EE Dept); ENGG 105: 4th Yr 1st or 4th Yr 2nd (EE Dept 2nd); ENGG 117: 4th Yr 1st (EE Dept) or 4th Yr 2nd
FLEXIBLE_SUBJECTS = [
    ("GEdE 101", "The Entrepreneurial Mind", 3, 3, 0, "", "", [(3, ""), (4, "EE Dept")]),
    ("ENGG 105", "Technopreneurship", 3, 3, 0, "4th Year Standing", "", [(9, ""), (10, "EE Dept")]),
    ("ENGG 117", "OJT/Internship (320 hrs)", 4, 4, 0, "4th Year Standing", "", [(9, "EE Dept"), (10, "")]),
]


def _cell_style():
    """Table cell style: Times New Roman, size 10, for course tables."""
    styles = getSampleStyleSheet()
    return ParagraphStyle(
        name="Cell",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=10,
        leading=11,
        wordWrap="CJK",
        leftIndent=0,
        rightIndent=0,
    )


def _header_cell_style():
    """Centered bold style for column headers so they fit and center in each column."""
    styles = getSampleStyleSheet()
    return ParagraphStyle(
        name="HeaderCell",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=10,
        alignment=1,  # TA_CENTER
        wordWrap="CJK",
        leftIndent=0,
        rightIndent=0,
    )


def _requisite_display_text(raw_text):
    """Insert line break before 'Standing' so PDF shows '4th Year' on one line and 'Standing' on the next."""
    if not raw_text:
        return raw_text
    return raw_text.replace(" Year Standing", " Year<br/>Standing")


def _cell_style_center():
    """Table cell style for Prerequisite/Co-requisite columns: centered text."""
    styles = getSampleStyleSheet()
    return ParagraphStyle(
        name="CellCenter",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=10,
        leading=11,
        alignment=1,  # CENTER
        wordWrap="CJK",
        leftIndent=0,
        rightIndent=0,
    )


def _resolve_logo_path(logo_path):
    """Return an absolute path to the logo file, or None. Tries fallback relative to this module."""
    if logo_path:
        p = os.path.abspath(os.path.normpath(str(logo_path)))
        if os.path.isfile(p):
            return p
    _root = os.path.dirname(os.path.abspath(__file__))
    fallback = os.path.join(_root, "static", "img", "BSU_Logo.png")
    if os.path.isfile(fallback):
        return fallback
    return None


def _logo_image(logo_path, max_width=1.1 * inch, max_height=1.1 * inch):
    """Return a platypus Image with aspect ratio preserved, or None if invalid."""
    path = _resolve_logo_path(logo_path)
    if not path:
        return None
    try:
        reader = ImageReader(path)
        iw, ih = reader.getSize()
        if iw <= 0:
            return None
        scale = min(max_width / float(iw), max_height / float(ih), 1.0)
        w, h = iw * scale, ih * scale
        return Image(path, width=w, height=h)
    except Exception:
        return None


def _header_flowables(logo_path=None):
    """
    BSU header: logo (if path given) + centered text, or centered text only.
    - Republic of the Philippines: Times New Roman 12
    - The National Engineering University: Arial 16 red
    - Alangilan Campus: Times 12
    - Address + Tel: Times 10
    - E-mail line: Times 10, email in blue
    - Curriculum block: Times 12
    """
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    p_republic = ParagraphStyle(
        name="Republic",
        parent=normal,
        alignment=1,
        fontName="Times-Roman",
        fontSize=12,
        spaceAfter=2,
    )
    p_bsu = ParagraphStyle(
        name="BSU",
        parent=normal,
        alignment=1,
        fontName="Times-Bold",
        fontSize=12,
        spaceAfter=2,
    )
    p_national = ParagraphStyle(
        name="National",
        parent=normal,
        alignment=1,
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=colors.HexColor("#CC0000"),
        spaceAfter=2,
    )
    p_campus = ParagraphStyle(
        name="Campus",
        parent=normal,
        alignment=1,
        fontName="Times-Roman",
        fontSize=12,
        spaceAfter=2,
    )
    p_address = ParagraphStyle(
        name="Address",
        parent=normal,
        alignment=1,
        fontName="Times-Roman",
        fontSize=10,
        spaceAfter=1,
    )
    email_para = Paragraph(
        'E-mail Address: <font color="blue">coe.alangilan@g.batstate-u.edu.ph</font> | Website Address: http://www.batstate-u.edu.ph',
        ParagraphStyle(
            name="Email",
            parent=normal,
            alignment=1,
            fontName="Times-Roman",
            fontSize=10,
            spaceAfter=6,
        )
    )
    p_college = ParagraphStyle(
        name="College",
        parent=normal,
        alignment=0,
        fontName="Times-Bold",
        fontSize=12,
        spaceAfter=4,
    )
    p_curriculum = ParagraphStyle(
        name="Curriculum",
        parent=normal,
        alignment=1,
        fontName="Times-Roman",
        fontSize=12,
        leading=12 * 1.15,  # line spacing 1.15
        spaceAfter=2,
    )
    p_curriculum_bold = ParagraphStyle(
        name="CurriculumBold",
        parent=normal,
        alignment=1,
        fontName="Times-Bold",
        fontSize=12,
        leading=12 * 1.15,  # line spacing 1.15
        spaceAfter=4,
    )

    return [
        Paragraph("CURRICULUM<br/>BACHELOR OF SCIENCE IN COMPUTER ENGINEERING", p_curriculum_bold),
        Paragraph(
            "Reference CMOs: CMO No. 87 s. 2017, CMO No. 4 s. 2018 and CMO No. 20,<br/>"
            "s. 2013, CMO 39, s. 2021, CMO 40, s. 2021",
            p_curriculum,
        ),
        Spacer(1, 0.08 * inch),
        Paragraph("BS COMPUTER ENGINEERING<br/>PROGRAM OF STUDY<br/>EVALUATION", p_curriculum_bold),
        Spacer(1, 0.12 * inch),
    ]


def _draw_logo_on_canvas(canvas, logo_path, max_w=1.25 * inch, max_h=1.25 * inch):
    """Draw logo in top-left of first page; no layout space. Preserve aspect ratio."""
    path = _resolve_logo_path(logo_path)
    if not path:
        return
    try:
        reader = ImageReader(path)
        iw, ih = reader.getSize()
        if iw <= 0:
            return
        scale = min(max_w / float(iw), max_h / float(ih), 1.0)
        w, h = iw * scale, ih * scale
        x = 0.65 * inch
        y = legal[1] - 0.35 * inch - h  # a bit higher
        canvas.saveState()
        canvas.drawImage(path, x, y, width=w, height=h)
        canvas.restoreState()
    except Exception:
        pass


def _footer_canvas(canvas, doc):
    """Draw slogan at bottom of each page: italic, bold, bigger font, Arial (Helvetica)."""
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#CC0000"))  # same red as "The National Engineering University"
    canvas.setFont("Helvetica-BoldOblique", 12)  # Arial bold italic, bigger
    canvas.drawCentredString(legal[0] / 2, 0.4 * inch, "Leading Innovations, Transforming Lives, Building the Nation")
    canvas.restoreState()


def _page_header_canvas(canvas, logo_path):
    """Draw repeating header at top of every page (keeps original colors, incl. blue email)."""
    canvas.saveState()
    left = 0.5 * inch
    right = legal[0] - 0.5 * inch
    width = right - left

    _draw_logo_on_canvas(canvas, logo_path)
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    p_republic = ParagraphStyle("HdrRepublic", parent=normal, alignment=1, fontName="Times-Roman", fontSize=12, leading=12, spaceAfter=2)
    p_bsu = ParagraphStyle("HdrBSU", parent=normal, alignment=1, fontName="Times-Bold", fontSize=12, leading=12, spaceAfter=2)
    p_national = ParagraphStyle("HdrNational", parent=normal, alignment=1, fontName="Helvetica-Bold", fontSize=9, leading=10, textColor=colors.HexColor("#CC0000"), spaceAfter=2)
    p_campus = ParagraphStyle("HdrCampus", parent=normal, alignment=1, fontName="Times-Roman", fontSize=12, leading=12, spaceAfter=2)
    p_address = ParagraphStyle("HdrAddress", parent=normal, alignment=1, fontName="Times-Roman", fontSize=10, leading=11, spaceAfter=1)
    p_email = ParagraphStyle("HdrEmail", parent=normal, alignment=1, fontName="Times-Roman", fontSize=10, leading=11, spaceAfter=6)

    header_flow = [
        Paragraph("Republic of the Philippines", p_republic),
        Paragraph("BATANGAS STATE UNIVERSITY", p_bsu),
        Paragraph("The National Engineering University", p_national),
        Paragraph("Alangilan Campus", p_campus),
        Paragraph(
            "Golden Country Homes, Alangilan Batangas City, Batangas, Philippines 4200<br/>"
            "Tel Nos.: (+63 43) 425-0139 local 2121 / 2221",
            p_address,
        ),
        Paragraph(
            'E-mail Address: <u><font color="blue">coe.alangilan@g.batstate-u.edu.ph</font></u> | '
            'Website Address: http://www.batstate-u.edu.ph',
            p_email,
        ),
    ]

    y = legal[1] - 0.55 * inch
    for para in header_flow:
        w, h = para.wrap(width, 10 * inch)
        y -= h
        para.drawOn(canvas, left, y)

    y -= 4
    canvas.setLineWidth(0.5)
    canvas.setStrokeColor(colors.black)
    canvas.line(left, y, right, y)

    y -= 14
    canvas.setFont("Times-Bold", 12)
    canvas.setFillColor(colors.black)
    canvas.drawString(left, y, "College of Engineering")
    canvas.restoreState()


def _course_lookup_from_curriculum():
    """Build code -> (title, units, lec, lab, prereq, coreq) from first occurrence in POS_CURRICULUM."""
    lookup = {}
    for _section_index, (_t1, _t2, _tu, _lec_t, _lab_t, rows) in enumerate(POS_CURRICULUM):
        for r in rows:
            code = r[0]
            if code not in lookup:
                lookup[code] = (r[1], r[2], r[3], r[4], r[5] or "", r[6] or "")
    return lookup


def build_pos_pdf(student_name, sr_code, academic_year, grades_by_section=None, grades_dict=None, eval_date=None, logo_path=None):
    """
    Build POS PDF. Use grades_by_section (dict section_index -> {course_code: grade}) so each section table
    shows that section's grades. Each section lists all courses with a grade in that section (same course
    can appear in multiple sections, e.g. MATH 102 failed in 1st sem and midterm; user-added courses appear).
    If grades_by_section is None, falls back to grades_dict for all sections (backward compat).
    """
    if eval_date is None:
        eval_date = date.today()
    date_str = eval_date.strftime("%B %d, %Y")

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=legal,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=2.15 * inch,
        bottomMargin=1.0 * inch,
    )

    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    cell_style = _cell_style()
    cell_style_center = _cell_style_center()
    header_cell_style = _header_cell_style()
    hours_header_style = ParagraphStyle(
        name="HoursHeader",
        parent=normal,
        fontName="Times-Bold",
        fontSize=8,
        alignment=1,
        leading=9,
    )
    section_line1 = ParagraphStyle(
        name="Section1",
        parent=normal,
        fontSize=10,
        fontName="Times-Bold",
        spaceAfter=0,
        spaceBefore=12,
    )
    section_line2 = ParagraphStyle(
        name="Section2",
        parent=normal,
        fontSize=10,
        fontName="Times-Bold",
        alignment=1,
        spaceAfter=6,
        spaceBefore=0,
    )
    evaluated_style = ParagraphStyle(
        name="Evaluated",
        parent=normal,
        fontName="Times-Roman",
        fontSize=10,
        alignment=2,  # RIGHT
        spaceAfter=14,
        spaceBefore=10,  # extra space above "Evaluated by"
    )
    sign_style = ParagraphStyle(
        name="SignLabel",
        parent=normal,
        fontName="Times-Roman",
        fontSize=10,
        alignment=0,  # LEFT
        spaceAfter=2,
        spaceBefore=6,
    )
    sign_name_style = ParagraphStyle(
        name="SignName",
        parent=normal,
        fontName="Times-Bold",
        fontSize=10,
        alignment=0,
        spaceAfter=0,
        spaceBefore=0,
    )

    story = []
    story.extend(_header_flowables(logo_path))

    info_cols = [
        0.84 * inch,
        3.50 * inch,
        1.24 * inch,
        1.62 * inch,
    ]
    info_data = [
        ["Name:", (student_name or "—").upper(), "Date:", date_str],
        ["SR-code:", (sr_code or "—"), "Academic Year:", (academic_year or "—")],
    ]
    info_tbl = Table(info_data, colWidths=info_cols)
    info_tbl.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
            ("FONTSIZE", (0, 0), (-1, -1), 11),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )
    story.append(info_tbl)
    story.append(Spacer(1, 0.25 * inch))

    col_widths = [
        0.88 * inch,
        2.35 * inch,
        0.57 * inch,
        0.39 * inch,
        0.39 * inch,
        1.08 * inch,
        1.01 * inch,
        0.53 * inch,
    ]

    course_lookup = _course_lookup_from_curriculum()

    def _grade_cell(grade_str):
        if not grade_str:
            return grade_str
        try:
            if float(grade_str) == 5.0:
                return Paragraph(
                    '<font color="red">' + grade_str.replace("&", "&amp;") + '</font>',
                    ParagraphStyle(name="GradeRed", parent=normal, fontName="Times-Roman", fontSize=10, alignment=1),
                )
        except (ValueError, TypeError):
            pass
        return grade_str

    for section_index, (title1, title2, total_units, lec_total, lab_total, rows) in enumerate(POS_CURRICULUM):
        section_grades = {}
        if grades_by_section is not None:
            section_grades = grades_by_section.get(section_index) or grades_by_section.get(str(section_index)) or {}
        else:
            section_grades = grades_dict or {}
        title_year_para = Paragraph("<b>" + title1 + "</b>", ParagraphStyle(
            name="YearTitle", parent=normal, alignment=1, fontName="Times-Bold", fontSize=10,
        ))
        title_sem_para = Paragraph("<b>" + title2 + "</b>", ParagraphStyle(
            name="SemTitle", parent=normal, alignment=1, fontName="Times-Bold", fontSize=10,
        ))
        # Table: rows 0-1 = Year/Semester; rows 2-3 = two-row header (No. of hour/s only above Lec+Lab)
        table_data = [
            [title_year_para] + [""] * (len(col_widths) - 1),
            [title_sem_para] + [""] * (len(col_widths) - 1),
            [
                Paragraph("Course Code", header_cell_style),
                Paragraph("Course Title", header_cell_style),
                Paragraph("Credit Units", header_cell_style),
                Paragraph("No. of hour/s", hours_header_style),
                "",
                Paragraph("Prerequisite(s)", header_cell_style),
                Paragraph("Co-requisite(s)", header_cell_style),
                Paragraph("Grade", header_cell_style),
            ],
            ["", "", "", Paragraph("Lec", header_cell_style), Paragraph("Lab", header_cell_style), "", "", ""],
        ]
        curriculum_codes_this_section = {r[0] for r in rows}
        section_rows = []
        seen_codes = set()
        for code, title, units, lec, lab, prereq, coreq in rows:
            grade = (section_grades or {}).get(code, "").strip() or ""
            if grade:
                seen_codes.add(code)
                section_rows.append((code, title, units, lec, lab, prereq or "", coreq or "", grade))
        for code, grade in (section_grades or {}).items():
            if not code or not (grade or "").strip():
                continue
            if code in seen_codes:
                continue
            seen_codes.add(code)
            info = course_lookup.get(code)
            if info:
                title, units, lec, lab, prereq, coreq = info
            else:
                title, units, lec, lab, prereq, coreq = code, 0, 0, 0, "", ""
            section_rows.append((code, title, units, lec, lab, prereq, coreq, (grade or "").strip()))
        if not section_rows:
            continue
        sum_units = sum(r[2] for r in section_rows)
        sum_lec = sum(r[3] for r in section_rows)
        sum_lab = sum(r[4] for r in section_rows)
        for code, title, units, lec, lab, prereq, coreq, grade in section_rows:
            table_data.append([
                Paragraph(code.replace("&", "&amp;"), cell_style),
                Paragraph(title.replace("&", "&amp;"), cell_style),
                str(units),
                str(lec),
                str(lab),
                Paragraph(_requisite_display_text(prereq).replace("&", "&amp;") or " ", cell_style_center),
                Paragraph(_requisite_display_text(coreq).replace("&", "&amp;") or " ", cell_style_center),
                _grade_cell(grade),
            ])
        table_data.append(["Total", "", str(sum_units), str(sum_lec), str(sum_lab), "", "", ""])

        t = Table(table_data, colWidths=col_widths, repeatRows=0)
        ncols = len(col_widths)
        t.setStyle(
            TableStyle([
                ("SPAN", (0, 0), (ncols - 1, 0)),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F5F5DC")),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
                ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"),
                ("TOPPADDING", (0, 0), (-1, 0), 5),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                ("SPAN", (0, 1), (ncols - 1, 1)),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#E0E0E0")),
                ("ALIGN", (0, 1), (-1, 1), "CENTER"),
                ("VALIGN", (0, 1), (-1, 1), "MIDDLE"),
                ("FONTNAME", (0, 1), (-1, 1), "Times-Bold"),
                ("TOPPADDING", (0, 1), (-1, 1), 5),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
                ("SPAN", (0, 2), (0, 3)),
                ("SPAN", (1, 2), (1, 3)),
                ("SPAN", (2, 2), (2, 3)),
                ("SPAN", (3, 2), (4, 2)),
                ("SPAN", (5, 2), (5, 3)),
                ("SPAN", (6, 2), (6, 3)),
                ("SPAN", (7, 2), (7, 3)),
                ("FONTNAME", (0, 2), (-1, 3), "Times-Bold"),
                ("ALIGN", (0, 2), (-1, 3), "CENTER"),
                ("VALIGN", (0, 2), (-1, 3), "MIDDLE"),
                ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.black),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
                ("LINEBELOW", (0, 1), (-1, 1), 0.5, colors.black),
                ("LINEABOVE", (0, 2), (-1, 2), 0.5, colors.black),
                ("LINEBELOW", (0, 3), (-1, 3), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.black),
                ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.black),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("INNERGRID", (0, 2), (-1, -1), 0.5, colors.black),
                ("ALIGN", (2, 2), (4, -1), "CENTER"),
                ("ALIGN", (5, 2), (6, -1), "CENTER"),
                ("ALIGN", (7, 2), (7, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 2), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("FONTNAME", (0, -1), (-1, -1), "Times-Bold"),
            ])
        )
        story.append(t)
        story.append(Paragraph("Evaluated by: ______________________________", evaluated_style))

    story.append(Spacer(1, 0.4 * inch))
    story.append(Paragraph("Checked and Reviewed:", sign_style))
    story.append(Paragraph("<b>Engr. MARK JOHN FEL T. RAYOS</b>", sign_name_style))
    story.append(Paragraph("Program Chair, CpE Program", sign_style))
    story.append(Paragraph("Date:", sign_style))
    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph("Approved by:", sign_style))
    story.append(Paragraph("<b>Engr. RICMART V. GARBIN</b>", sign_name_style))
    story.append(Paragraph("Department Chair, EE Department", sign_style))
    story.append(Paragraph("Date:", sign_style))

    def page_canvas(canvas, doc):
        _page_header_canvas(canvas, logo_path)
        _footer_canvas(canvas, doc)

    doc.build(story, onFirstPage=page_canvas, onLaterPages=page_canvas)
    buffer.seek(0)
    return buffer.getvalue()
