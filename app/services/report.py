"""
PDF attendance report in the school's "STUDENT'S ATTENDANCE SHEET" format:

    [seal]  Republic of the Philippines / <University> / Province of ...
            College of ________
            STUDENT'S ATTENDANCE SHEET
    Subject ______  Course/Sec. ______  AY ______  Sem. ____  Summer ____
    Schedule ______________________     Instructor/Professor ______________
    # | Name | 6 date columns (+ async) | No. of Absences | Remarks      (rows 1-40+)
    Prof./Instructor's Signature row
    *Use signature only                      Noted by: ____________

Cells: student's signature = present (+ small "L" = late) · A = absent · E = excused
       blank = not held yet / not enrolled yet. If a student has no signature on file: "P".
Layout settings (school lines, paper size, rows) are in config.py (REPORT_*).
"""
import io
import logging
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from reportlab.pdfbase.pdfmetrics import stringWidth

from . import checkin_signatures
from . import classes as classes_svc
from . import sessions as sessions_svc
from .accounts import ValidationError

log = logging.getLogger(__name__)

MAX_DATES = 6           # date columns per page (more dates -> more pages)
MAX_ASYNC = 3           # async columns per page
MAX_SELECT_DATES = 60   # sanity limits for one report
MAX_SELECT_ASYNC = 30
REPORTABLE_PHASES = ("ended", "present", "late", "checkout")   # ended + currently open; never upcoming


# ================================================================ choosing sessions
def reportable_sessions(conn, section_id, now=None):
    """Sessions that can go in a report (oldest first): only ENDED ones and ones open RIGHT NOW
    (never upcoming). Default selection: the 6 most recent dates + the 3 most recent async."""
    now = now or datetime.now()
    rows = sessions_svc.list_section_sessions(conn, section_id, now)          # newest first
    rows = [r for r in rows if r["phase"] in REPORTABLE_PHASES]
    regular = [r for r in rows if r["type"] != "async"]
    asyncs = [r for r in rows if r["type"] == "async"]
    default = {r["id"] for r in regular[:MAX_DATES]} | {r["id"] for r in asyncs[:MAX_ASYNC]}
    return [{**r, "default": r["id"] in default}
            for r in sorted(rows, key=lambda x: (x["session_date"], x["start_time"] or ""))]


MAX_BLANK_DATES = 12   # "blank dates" per report (empty columns to fill in on paper)


def blank_columns(values) -> list[dict]:
    """Dates the instructor typed in ("Blank dates"): empty columns with every student's name,
    for printing the sheet and taking attendance on paper. Any date (past or future) is fine.
    They look like sessions to the PDF code but have no records: id 'blank-YYYY-MM-DD'."""
    days = set()
    for v in values:
        v = (v or "").strip()
        if not v:
            continue
        try:
            days.add(datetime.strptime(v, "%Y-%m-%d").strftime("%Y-%m-%d"))
        except ValueError:
            raise ValidationError({"sessions": f"“{v}” is not a valid date."})
    if len(days) > MAX_BLANK_DATES:
        raise ValidationError({"sessions": f"Add at most {MAX_BLANK_DATES} blank dates per report."})
    return [{"id": f"blank-{d}", "session_date": d, "type": "regular", "start_time": None, "blank": True}
            for d in sorted(days)]


def validate_selection(conn, section_id, ids, now=None, blank_dates=()) -> list[dict]:
    """Returns the chosen sessions (dates oldest first, then async) or raises ValidationError.
    blank_dates: extra empty date columns (see blank_columns)."""
    try:
        ids = {int(i) for i in ids}
    except (TypeError, ValueError):
        raise ValidationError({"sessions": "Invalid selection."})
    blanks = blank_columns(blank_dates)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM class_sessions WHERE section_id = ? AND deleted_at IS NULL", (section_id,))
        if r["id"] in ids]
    if len(rows) != len(ids) or not (rows or blanks):
        raise ValidationError({"sessions": "Pick at least one date for the report."})
    now = now or datetime.now()
    if any(sessions_svc.describe(r, now)["phase"] not in REPORTABLE_PHASES for r in rows):
        raise ValidationError({"sessions": "Upcoming sessions can't go in the report yet — only ended or current ones."})
    regular = [r for r in rows if r["type"] != "async"] + blanks
    asyncs = [r for r in rows if r["type"] == "async"]
    if len(regular) > MAX_SELECT_DATES:
        raise ValidationError({"sessions": f"Pick at most {MAX_SELECT_DATES} dates per report."})
    if len(asyncs) > MAX_SELECT_ASYNC:
        raise ValidationError({"sessions": f"Pick at most {MAX_SELECT_ASYNC} async activities per report."})
    return (sorted(regular, key=lambda r: (r["session_date"], r["start_time"] or ""))
            + sorted(asyncs, key=lambda r: r["session_date"]))


# ================================================================ data
def build_matrix(conn, section, chosen, now=None):
    """students (sorted by name) x chosen sessions -> status per cell."""
    now = now or datetime.now()
    students, cells = {}, {}
    for s in chosen:
        if s.get("blank"):                 # blank date column: nothing recorded, filled in on paper
            continue
        full = dict(conn.execute(sessions_svc.SESSION_SELECT + " WHERE cs.id = ?", (s["id"],)).fetchone())
        for r in sessions_svc.roster(conn, full, now):
            students[r["id"]] = r
            cells[(r["id"], s["id"])] = r["status"]
    for r in conn.execute(
        """SELECT u.id, u.student_number, u.first_name, u.middle_name, u.last_name, u.suffix
           FROM enrollments e JOIN users u ON u.id = e.student_id
           WHERE e.section_id = ? AND e.status = 'active' AND e.deleted_at IS NULL AND u.deleted_at IS NULL""",
        (section["id"],)):
        students.setdefault(r["id"], dict(r))
    ordered = sorted(students.values(), key=lambda r: (r["last_name"].lower(), r["first_name"].lower()))
    return ordered, cells


def _signature_files(conn, user_ids, signature_dir: Path):
    out = {}
    if not user_ids:
        return out
    q = ",".join("?" * len(user_ids))
    for r in conn.execute(f"SELECT id, signature_path FROM users WHERE id IN ({q})", list(user_ids)):
        p = signature_dir / f"{r['id']}.png"
        if r["signature_path"] and p.exists():
            out[r["id"]] = p
    return out


# ================================================================ PDF helpers
def _school_font(candidates) -> str:
    """Register Old English Text MT if it's on this computer; else fall back to Times-Bold."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "OldEnglish" in pdfmetrics.getRegisteredFontNames():
        return "OldEnglish"
    for path in candidates or []:
        if path and Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont("OldEnglish", str(path)))
                return "OldEnglish"
            except Exception:
                log.exception("could not load school font %s", path)
    return "Times-Bold"



def _name(r):
    """'Last, First Middle Suffix' - like the handwritten sheet."""
    from .accounts import normalize_suffix
    first = " ".join(p for p in (r["first_name"], r.get("middle_name"), normalize_suffix(r.get("suffix"))) if p)
    return f"{r['last_name']}, {first}"


def _fit(path, w, h):
    img = Image(str(path))
    scale = min(w / img.imageWidth, h / img.imageHeight)
    img.drawWidth, img.drawHeight = img.imageWidth * scale, img.imageHeight * scale
    return img


def _schedule_text(conn, section_id):
    def t(hhmm):
        return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M").lstrip("0")
    return ", ".join(f"{s['day_name'][:3].upper()} {t(s['start_time'])}-{t(s['end_time'])}"
                     for s in classes_svc.list_schedules(conn, section_id))


def _college(instructor):
    dept = (instructor.get("department") or "").strip()
    return dept[len("College of "):] if dept.lower().startswith("college of ") else dept


def _field(label, value, label_w, value_w, st, big=False):
    """'Label ______value______' as a tiny table with an underline under the value.
    big=True uses 12 pt (the "College of" line)."""
    ls, vs = (st["college_label"], st["college_value"]) if big else (st["label"], st["value"])
    t = Table([[Paragraph(label, ls), Paragraph(value or "&nbsp;", vs)]],
              colWidths=[label_w, value_w])
    t.setStyle(TableStyle([("LINEBELOW", (1, 0), (1, 0), 0.6, colors.black),
                           ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return t


# ================================================================ PDF
def build_pdf(conn, section, instructor, chosen, signature_dir: Path, *, school_lines=None,
              page_inches=(8.27, 11.69), min_rows=40, logo_path: Path | None = None,
              include_prof_signature: bool = False, margins_inches=(1.0, 1.0, 0.4, 0.45),
              school_font_files=None, now=None, include_department: bool = True) -> bytes:
    """include_prof_signature: put the instructor's saved signature in the
    "Prof./Instructor's Signature" row. Off by default -> the row stays blank to sign by hand.
    include_department: print the instructor's department after "College of"; off -> blank line."""
    now = now or datetime.now()
    school_lines = school_lines or []
    students, cells = build_matrix(conn, section, chosen, now)
    sigs = _signature_files(conn, [s["id"] for s in students] + [instructor["id"]], signature_dir)
    prof_sig = sigs.get(instructor["id"]) if include_prof_signature else None
    # web check-ins were signed on the spot: that drawing wins over the stored signature
    checkin_sigs = checkin_signatures.files_for_sessions(
        conn, [s["id"] for s in chosen if not s.get("blank")], signature_dir)

    school_font = _school_font(school_font_files)
    st = {
        "school1": ParagraphStyle("s1", fontName="Helvetica", fontSize=10, leading=12, alignment=TA_CENTER),
        "school2": ParagraphStyle("s2", fontName=school_font, fontSize=16 if school_font == "OldEnglish" else 15,
                                  leading=19, alignment=TA_CENTER),
        "title": ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=10, leading=13, alignment=TA_CENTER),
        "college_label": ParagraphStyle("cl", fontName="Helvetica-Bold", fontSize=10, leading=12),
        "college_value": ParagraphStyle("cv", fontName="Helvetica", fontSize=10, leading=12, alignment=TA_CENTER),
        "college_full": ParagraphStyle("cf", fontName="Helvetica-Bold", fontSize=10.5, leading=13, alignment=TA_CENTER),
        "label": ParagraphStyle("l", fontName="Helvetica-Bold", fontSize=8.5, leading=10),
        "value": ParagraphStyle("v", fontName="Helvetica", fontSize=9, leading=11),
        "head": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=8, leading=9.5, alignment=TA_CENTER),
        "name": ParagraphStyle("n", fontName="Helvetica", fontSize=8, leading=9),
        "mark": ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=8.5, leading=9.5, alignment=TA_CENTER),
        "tiny": ParagraphStyle("y", fontName="Helvetica-Oblique", fontSize=7.5, leading=9),
        "noted": ParagraphStyle("o", fontName="Helvetica-Bold", fontSize=9, leading=11),
    }

    page_w, page_h = page_inches[0] * inch, page_inches[1] * inch
    m_left, m_right, m_top, m_bottom = (x * inch for x in margins_inches)
    usable = page_w - m_left - m_right
    k = usable / (7.6 * inch)            # header fields were laid out for 7.6in; scale to the real width

    sem = section["semester"]
    prof = " ".join(p for p in (instructor.get("title"), instructor["first_name"],
                                instructor.get("middle_name"), instructor["last_name"]) if p)
    schedule_text = _schedule_text(conn, section["id"])

    # ---------------------------------------------------------------- header (new objects per page)
    def header_block():
        # Like the printed form: the seal sits just left of the school name, and the school name
        # is centered on the page (same center as the title below).
        lines = [Paragraph(text, st["school2"] if i == 1 else st["school1"]) for i, text in enumerate(school_lines)]
        if logo_path and logo_path.exists() and lines:
            logo = _fit(logo_path, 0.85 * inch, 0.85 * inch)
            text_w, side_w = 3.75 * inch, 0.95 * inch
            head = Table([[logo, lines, ""]], colWidths=[side_w, text_w, side_w], hAlign="CENTER")
            head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 0), "RIGHT"),
                                      ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                      ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
            out = [head]
        else:
            out = list(lines)
        name = _college(instructor) if include_department else ""
        if name:
            # printed department: one clean, centered line, only the department name underlined
            # ("College of Engineering") - no long blank line
            college = Paragraph(f"College of <u>{name}</u>", st["college_full"])
        else:
            # left blank to write by hand: "College of ______"
            # (label cell only as wide as the words, so the underline starts right after it)
            label_w = stringWidth("College of", "Helvetica-Bold", 10) + 7
            college = _field("College of", "", label_w, 2.6 * inch, st, big=True)
            college.hAlign = "CENTER"
        out += [Spacer(1, 3), college, Spacer(1, 5), Paragraph("STUDENT'S ATTENDANCE SHEET", st["title"])]
        # labels keep their width; the blank lines shrink/grow with the page width
        row1 = Table([[_field("Subject", section["subject_code"], 0.55 * inch, 1.0 * k * inch, st),
                       _field("Course/Sec.", section["name"], 0.8 * inch, 1.05 * k * inch, st),
                       _field("AY", section["school_year"], 0.25 * inch, 0.8 * k * inch, st),
                       _field("Sem.", sem if sem != "Summer" else "", 0.38 * inch, 0.35 * inch, st),
                       _field("Summer", "X" if sem == "Summer" else "", 0.55 * inch, 0.3 * inch, st)]],
                     hAlign="LEFT")
        row2 = Table([[_field("Schedule", schedule_text, 0.65 * inch, 2.7 * k * inch, st),
                       _field("Instructor/Professor", prof, 1.35 * inch, 2.05 * k * inch, st)]], hAlign="LEFT")
        for t in (row1, row2):
            t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
        return out + [Spacer(1, 8), row1, Spacer(1, 4), row2, Spacer(1, 8)]

    # ---------------------------------------------------------------- pages
    # Each page is a full sheet: 6 date columns (+ up to 3 async) x 40 numbered rows.
    #   more than 6 dates   -> another page for the next 6
    #   more than 40 students -> another page for the next 40 (numbering continues: 41, 42...)
    # A page with fewer than 6 dates keeps the rest of the date columns empty to fill in by hand.
    dates = [s for s in chosen if s["type"] != "async"]
    asyncs = [s for s in chosen if s["type"] == "async"]
    date_groups = [dates[i:i + MAX_DATES] for i in range(0, len(dates), MAX_DATES)] or [[]]
    async_groups = [asyncs[i:i + MAX_ASYNC] for i in range(0, len(asyncs), MAX_ASYNC)]
    n_col_pages = max(len(date_groups), len(async_groups))
    col_pages = []
    for i in range(n_col_pages):
        dg = date_groups[i] if i < len(date_groups) else []
        ag = async_groups[i] if i < len(async_groups) else []
        col_pages.append(dg + [None] * (MAX_DATES - len(dg)) + ag)
    rows_per_page = max(1, min_rows)
    student_pages = [students[i:i + rows_per_page] for i in range(0, len(students), rows_per_page)] or [[]]

    no_w, abs_w, rem_w = 0.3 * inch, 0.68 * inch, 0.66 * inch
    # Row height adapts to the paper so 40 rows + the bottom part fit on one page.
    # ~4.05in = header block + table header + signature row + "Noted by".
    room = page_h - m_top - m_bottom - 4.05 * inch
    row_h = max(0.15 * inch, min(0.205 * inch, room / rows_per_page))

    def date_label(s):
        if s is None:
            return ""
        d = datetime.strptime(s["session_date"], "%Y-%m-%d")
        label = f"{d.month}/{d.day:02d}"
        if s["type"] == "async":
            return Paragraph(f"{label}<br/><font size=6>Async</font>", st["head"])
        if s["type"] == "makeup":
            return Paragraph(f"{label}<br/><font size=6>Make-up</font>", st["head"])
        return Paragraph(label, st["head"])

    def sheet_table(cols, studs, first_no):
        n = len(cols)
        name_w = min(2.35 * inch, usable * 0.3) if n <= 6 else min(2.0 * inch, usable * 0.27)
        col_w = (usable - no_w - name_w - abs_w - rem_w) / n
        data = [["", Paragraph("Name", st["head"])] + [date_label(s) for s in cols]
                + [Paragraph("No. of<br/>Absences", st["head"]), Paragraph("Remarks", st["head"])]]
        for i, stu in enumerate(studs, start=first_no):
            row = [Paragraph(f"<font size=6.5>{i}.</font>", st["name"]), Paragraph(_name(stu), st["name"])]
            absences = 0
            for s in cols:
                if s is None:                                    # empty date column
                    row.append("")
                    continue
                status = cells.get((stu["id"], s["id"]))
                if status in ("present", "late"):
                    sig = checkin_sigs.get((stu["id"], s["id"])) or sigs.get(stu["id"])
                    if sig:
                        img = _fit(sig, col_w - (12 if status == "late" else 4), row_h - 2)
                        row.append(Table([[img, Paragraph("<font size=6>L</font>", st["mark"])]],
                                         colWidths=[col_w - 12, 10],
                                         style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                                ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                                                ("VALIGN", (0, 0), (-1, -1), "MIDDLE")])
                                   if status == "late" else img)
                    else:
                        row.append(Paragraph("L" if status == "late" else "P", st["mark"]))
                elif status == "absent":
                    absences += 1
                    row.append(Paragraph("A", st["mark"]))
                elif status == "excused":
                    row.append(Paragraph("E", st["mark"]))
                else:
                    row.append("")                               # not held yet / not enrolled yet
            row += [Paragraph(str(absences) if absences else "", st["mark"]), ""]
            data.append(row)
        for i in range(first_no + len(studs), first_no + rows_per_page):     # empty numbered rows
            data.append([Paragraph(f"<font size=6.5>{i}.</font>", st["name"]), ""] + [""] * n + ["", ""])

        # instructor signature row (only when the instructor chose to include it)
        sig_row = [Paragraph("<font size=7>Prof./Instructor's Signature</font>", st["name"]), ""]
        for s in cols:
            held = s is not None and any(cells.get((stu["id"], s["id"])) not in (None, "pending") for stu in students)
            sig_row.append(_fit(prof_sig, col_w - 4, row_h + 0.06 * inch) if prof_sig and held else "")
        data.append(sig_row + ["", ""])

        widths = [no_w, name_w] + [col_w] * n + [abs_w, rem_w]
        heights = [0.36 * inch] + [row_h] * (len(data) - 2) + [row_h + 0.1 * inch]
        table = Table(data, colWidths=widths, rowHeights=heights)
        last = len(data) - 1
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("SPAN", (0, last), (1, last)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (2, 1), (-1, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return table

    def noted_by():
        noted = Table([[Paragraph("Noted by:", st["noted"]), ""], ["", ""]],
                      colWidths=[0.8 * inch, 2.6 * inch], rowHeights=[0.25 * inch, 0.3 * inch], hAlign="LEFT")
        noted.setStyle(TableStyle([("LINEBELOW", (1, 1), (1, 1), 0.6, colors.black)]))
        return noted

    story = []
    for cols in col_pages:
        for p, studs in enumerate(student_pages):
            if story:
                story.append(PageBreak())
            story += header_block()
            story += [sheet_table(cols, studs, p * rows_per_page + 1), Spacer(1, 3),
                      Paragraph("*Use signature only", st["tiny"]), Spacer(1, 12), noted_by()]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawRightString(page_w - m_right, 0.28 * inch, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=(page_w, page_h), leftMargin=m_left, rightMargin=m_right,
                            topMargin=m_top, bottomMargin=m_bottom,
                            title=f"Student's Attendance Sheet - {section['subject_code']} {section['name']}", author=prof)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    log.info("report built: section=%s sessions=%s students=%s pages=%s", section["id"],
             [s["id"] for s in chosen], len(students), len(col_pages) * len(student_pages))
    return buf.getvalue()


def filename(section) -> str:
    safe = lambda s: "".join(ch for ch in s if ch.isalnum() or ch in "-_")
    return f"Attendance_{safe(section['subject_code'])}_{safe(section['name'])}_{datetime.now():%Y%m%d}.pdf"
