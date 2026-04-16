# Value = year level whose units must be 90% complete to get this standing (2nd standing = after year 1, etc.)
YEAR_STANDING_RULES = {
    # Value = year level whose units must be 90% complete
    # 2nd Year Standing: 90% of Year 1 units
    # 3rd Year Standing: 90% of Year 2 units
    # 4th Year Standing / Graduating: 90% of Year 3 units
    "2nd Year Standing": 1,
    "3rd Year Standing": 2,
    "4th Year Standing": 3,
    "Graduating": 3,
}

PREREQ_ALIASES = {
    "CpEE 401": ["Elective Course 1"],
    "CpEE 402": ["Elective Course 2"],
    "CpEE 403": ["Elective Course 3"],
}

courses = {
    # --- FIRST YEAR: 1st Semester ---
    "ENGG 101": {"course_title": "Introduction to Engineering", "units": 1, "pre-requisites": [], "co-requisites": [], "orig_sem": 1, "category": "Rigid", "year_level": 1},
    "GEd 108":  {"course_title": "Art Appreciation", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 1, "category": "Rigid", "year_level": 1},
    "GEd 104":  {"course_title": "The Contemporary World", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 1, "category": "Rigid", "year_level": 1},
    "CpE 101":  {"course_title": "Computer Programming", "units": 1, "pre-requisites": [], "co-requisites": [], "orig_sem": 1, "category": "Rigid", "year_level": 1},
    "GEd 105":  {"course_title": "Readings in Philippines History", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 1, "category": "Rigid", "year_level": 1},
    "MATH 101": {"course_title": "Differential Calculus", "units": 4, "pre-requisites": [], "co-requisites": [], "orig_sem": 1, "category": "Bottleneck", "year_level": 1},
    "CHEM 101": {"course_title": "Chemistry for Engineers", "units": 4, "pre-requisites": [], "co-requisites": [], "orig_sem": 1, "category": "Bottleneck", "year_level": 1},
    "PATHFit 1": {"course_title": "Movement Competency Training", "units": 2, "pre-requisites": [], "co-requisites": [], "orig_sem": 1, "category": "Rigid", "year_level": 1},
    "NSTP 111": {"course_title": "National Service Training Program 1", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 1, "category": "Rigid", "year_level": 1},

    # --- FIRST YEAR: 2nd Semester ---
    "GEd 101":  {"course_title": "Understanding the Self", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 2, "category": "Rigid", "year_level": 1},
    "GEd 102":  {"course_title": "Mathematics in the Modern World", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 2, "category": "Rigid", "year_level": 1},
    "GEd 106":  {"course_title": "Purposive Communication", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 2, "category": "Rigid", "year_level": 1},
    "GEd 109":  {"course_title": "Science, Technology and Society", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 2, "category": "Rigid", "year_level": 1},
    "MATH 102": {"course_title": "Integral Calculus", "units": 4, "pre-requisites": ["MATH 101"], "co-requisites": [], "orig_sem": 2, "category": "Bottleneck", "year_level": 1},
    "PHYS 102": {"course_title": "Physics for Engineers 1", "units": 4, "pre-requisites": ["MATH 101"], "co-requisites": ["MATH 102"], "orig_sem": 2, "category": "Bottleneck", "year_level": 1},
    "PATHFit 2": {"course_title": "Exercise-based Fitness Activities", "units": 2, "pre-requisites": ["PATHFit 1"], "co-requisites": [], "orig_sem": 2, "category": "Rigid", "year_level": 1},
    "NSTP 121": {"course_title": "National Service Training Program 2", "units": 3, "pre-requisites": ["NSTP 111"], "co-requisites": [], "orig_sem": 2, "category": "Rigid", "year_level": 1},
    "ENGG 102":  {"course_title": "Engineering Drawing", "units": 1, "pre-requisites": [], "co-requisites": [], "orig_sem": 2, "category": "Rigid", "year_level": 1},

    # --- FIRST YEAR: Midterm/Summer ---
    "GEd 103":  {"course_title": "Life and Works of Rizal", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 3, "category": "Rigid", "year_level": 1},
    "GEd 107":  {"course_title": "Ethics", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 3, "category": "Rigid", "year_level": 1},
    "PHYS 103": {"course_title": "Physics for Engineers 2", "units": 3, "pre-requisites": ["PHYS 102", "MATH 102"], "co-requisites": [], "orig_sem": 3, "category": "Bottleneck", "year_level": 1},

    # --- SECOND YEAR: 1st Semester ---
    "CpE 201":  {"course_title": "CpE Discipline, Professional Practice and laws", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 4, "category": "Rigid", "year_level": 2},
    "CpE 202":  {"course_title": "Discrete Mathematics", "units": 3, "pre-requisites": ["MATH 101"], "co-requisites": [], "orig_sem": 4, "category": "Rigid", "year_level": 2},
    "CpE 203":  {"course_title": "Programming Logic and Design", "units": 2, "pre-requisites": [], "co-requisites": [], "orig_sem": 4, "category": "Rigid", "year_level": 2},
    "EE 212":   {"course_title": "Fundamentals of Electrical Circuits", "units": 4, "pre-requisites": ["PHYS 103"], "co-requisites": [], "orig_sem": 4, "category": "Rigid", "year_level": 2},
    "ENGG 103": {"course_title": "Computer-Aided Design", "units": 1, "pre-requisites": ["ENGG 102"], "co-requisites": [], "orig_sem": 4, "category": "Rigid", "year_level": 2},
    "ENGG 104": {"course_title": "Engineering Economics", "units": 3, "pre-requisites": ["MATH 102"], "co-requisites": [], "orig_sem": 4, "category": "Rigid", "year_level": 2},
    "AI 101":   {"course_title": "Linear Algebra for AI", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 4, "category": "Rigid", "year_level": 2},
    "MATH 103": {"course_title": "Differential Equations", "units": 3, "pre-requisites": ["MATH 102"], "co-requisites": [], "orig_sem": 4, "category": "Bottleneck", "year_level": 2},
    "PATHFit 3": {"course_title": "Choice of dance, sports, Martial Arts, etc 1", "units": 2, "pre-requisites": ["PATHFit 1", "PATHFit 2"], "co-requisites": [], "orig_sem": 4, "category": "Rigid", "year_level": 2},
    "GEdE 101":  {"course_title": "The Entrepreneurial Mind", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 4, "category": "Rigid", "year_level": 2},

    # --- SECOND YEAR: 2nd Semester ---
    "CpE 204":  {"course_title": "Advance Engineering Mathematics for CpE", "units": 3, "pre-requisites": ["2nd Year Standing"], "co-requisites": [], "orig_sem": 5, "category": "Rigid", "year_level": 2},
    "CpE 205":  {"course_title": "Object-Oriented Programming", "units": 2, "pre-requisites": ["CpE 203"], "co-requisites": [], "orig_sem": 5, "category": "Rigid", "year_level": 2},
    "ECE 405":  {"course_title": "Electronic Circuits: Devices and Analysis", "units": 4, "pre-requisites": ["EE 212"], "co-requisites": [], "orig_sem": 5, "category": "Rigid", "year_level": 2},
    "MATH 104": {"course_title": "Numerical Methods", "units": 3, "pre-requisites": ["MATH 103"], "co-requisites": [], "orig_sem": 5, "category": "Rigid", "year_level": 2},
    "AI 102":   {"course_title": "Probability and Statistics for AI", "units": 3, "pre-requisites": ["MATH 102"], "co-requisites": [], "orig_sem": 5, "category": "Rigid", "year_level": 2},
    "PATHFit 4": {"course_title": "Choice of Dance, Sports, etc 2", "units": 2, "pre-requisites": ["PATHFit 1", "PATHFit 2"], "co-requisites": [], "orig_sem": 5, "category": "Rigid", "year_level": 2},
    "ENGG 114": {"course_title": "Basic Occupational Safety and Health", "units": 3, "pre-requisites": [], "co-requisites": [], "orig_sem": 5, "category": "Rigid", "year_level": 2},
    "Elective Course 1": {"course_title": "Elective Course 1", "units": 3, "pre-requisites": ["2nd Year Standing"], "co-requisites": [], "orig_sem": 5, "category": "Rigid", "year_level": 2},

    # --- THIRD YEAR: 1st Semester ---
    "CpE 302":  {"course_title": "Logic Circuits and Design", "units": 4, "pre-requisites": ["ECE 405"], "co-requisites": [], "orig_sem": 7, "category": "Rigid", "year_level": 3},
    "CpE 303":  {"course_title": "Data Structures and Algorithms", "units": 2, "pre-requisites": ["CpE 205"], "co-requisites": [], "orig_sem": 7, "category": "Rigid", "year_level": 3},
    "CpE 304":  {"course_title": "Introduction to Networks, Data and Digital Communications", "units": 3, "pre-requisites": ["ECE 405"], "co-requisites": ["CpE 302"], "orig_sem": 7, "category": "Rigid", "year_level": 3},
    "CpE 305":  {"course_title": "Fundamentals of Mixed Signals and Sensors", "units": 3, "pre-requisites": ["ECE 405"], "co-requisites": [], "orig_sem": 7, "category": "Rigid", "year_level": 3},
    "CpE 306":  {"course_title": "Feedback and Control Systems", "units": 3, "pre-requisites": ["EE 212"], "co-requisites": [], "orig_sem": 7, "category": "Rigid", "year_level": 3},
    "ENGG 116": {"course_title": "Methods of Research", "units": 3, "pre-requisites": ["AI 102", "3rd Year Standing"], "co-requisites": [], "orig_sem": 7, "category": "Rigid", "year_level": 3},
    "Elective Course 2": {"course_title": "Elective Course 2", "units": 3, "pre-requisites": ["CpEE 401"], "co-requisites": [], "orig_sem": 7, "category": "Rigid", "year_level": 3},

    # --- THIRD YEAR: 2nd Semester ---
    "CpE 308":  {"course_title": "Microprocessors", "units": 4, "pre-requisites": ["CpE 302"], "co-requisites": [], "orig_sem": 8, "category": "Rigid", "year_level": 3},
    "CpE 309":  {"course_title": "Software Design", "units": 4, "pre-requisites": ["CpE 303"], "co-requisites": [], "orig_sem": 8, "category": "Rigid", "year_level": 3},
    "CpE 310":  {"course_title": "Digital Signal Processing", "units": 4, "pre-requisites": ["CpE 305", "CpE 306"], "co-requisites": [], "orig_sem": 8, "category": "Rigid", "year_level": 3},
    "CpE 311":  {"course_title": "Computer Architecture and Organization", "units": 4, "pre-requisites": [], "co-requisites": ["CpE 308"], "orig_sem": 8, "category": "Rigid", "year_level": 3},
    "CpE 312":  {"course_title": "Connecting Networks and Security", "units": 4, "pre-requisites": ["CpE 304"], "co-requisites": [], "orig_sem": 8, "category": "Rigid", "year_level": 3},
    "AI 103":   {"course_title": "Machine Learning and Neural Networks", "units": 3, "pre-requisites": ["AI 101", "AI 102", "CpE 101"], "co-requisites": [], "orig_sem": 8, "category": "Rigid", "year_level": 3},

    # --- THIRD YEAR: Midterm ---
    "CpE 313":  {"course_title": "Introduction to HDL", "units": 1, "pre-requisites": ["CpE 203", "CpE 302"], "co-requisites": [], "orig_sem": 9, "category": "Rigid", "year_level": 3},
    "CpE 314":  {"course_title": "Emerging Technologies in CpE", "units": 3, "pre-requisites": ["3rd Year Standing"], "co-requisites": [], "orig_sem": 9, "category": "Rigid", "year_level": 3},
    "CpE 315":  {"course_title": "Computer Engineering Drafting and Design", "units": 1, "pre-requisites": ["ECE 405"], "co-requisites": [], "orig_sem": 9, "category": "Rigid", "year_level": 3},

    # --- FOURTH YEAR: 1st Semester ---
    "CpE 401":  {"course_title": "Operating Systems", "units": 3, "pre-requisites": ["CpE 303"], "co-requisites": [], "orig_sem": 10, "category": "Rigid", "year_level": 4},
    "CpE 402":  {"course_title": "Embedded Systems", "units": 4, "pre-requisites": ["CpE 308"], "co-requisites": [], "orig_sem": 10, "category": "Rigid", "year_level": 4},
    "CpE 403":  {"course_title": "Seminars and Fieldtrips", "units": 2, "pre-requisites": ["4th Year Standing"], "co-requisites": [], "orig_sem": 10, "category": "Rigid", "year_level": 4},
    "CpE 404":  {"course_title": "CpE Practice and Design 1", "units": 1, "pre-requisites": ["ENGG 116", "CpE 308"], "co-requisites": [], "orig_sem": 10, "category": "Rigid", "year_level": 4},
    "ENGG 106": {"course_title": "Engineering Management", "units": 2, "pre-requisites": ["4th Year Standing"], "co-requisites": [], "orig_sem": 10, "category": "Rigid", "year_level": 4},
    "ENGG 105": {"course_title": "Technopreneurship", "units": 3, "pre-requisites": ["4th Year Standing"], "co-requisites": [], "orig_sem": 10, "category": "Flexible", "year_level": 4},
    "Elective Course 3": {"course_title": "Elective Course 3", "units": 3, "pre-requisites": ["CpEE 402"], "co-requisites": [], "orig_sem": 10, "category": "Rigid", "year_level": 4},

    # --- FOURTH YEAR: 2nd Semester ---
    "ENGG 117": {"course_title": "OJT/Internship (320 hrs)", "units": 4, "pre-requisites": ["4th Year Standing"], "co-requisites": [], "orig_sem": 11, "category": "Flexible", "year_level": 4},
    "CpE 405":  {"course_title": "CpE Practice and Design 2", "units": 2, "pre-requisites": ["CpE 404"], "co-requisites": [], "orig_sem": 11, "category": "Rigid", "year_level": 4},
    "CpE 406":  {"course_title": "Comprehensive Examination for CpE", "units": 2, "pre-requisites": ["Graduating"], "co-requisites": [], "orig_sem": 11, "category": "Rigid", "year_level": 4},
}