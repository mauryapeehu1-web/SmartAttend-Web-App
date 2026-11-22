import csv
import io
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Flask, Response, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
DEFAULT_LOW_ATTENDANCE = 75
DEFAULT_LOW_MARKS = 50
STATUSES = ("Present", "Absent", "Leave", "Medical")

DEFAULT_SETTINGS = {
    "low_attendance": 75,
    "low_marks": 50,
    "allow_student_signup": True,
    "allow_teacher_signup": True,
    "teacher_can_mark": True,
    "teacher_can_edit": True,
    "teacher_can_reports": True,
    "teacher_allow_future_dates": False,
    "student_can_view_history": True,
    "student_can_view_trend": True,
    "student_can_view_alerts": True,
    "teacher_can_manage_academics": True,
    "student_can_view_marks": True,
    "student_can_view_class_avg": True,
    "student_can_view_assignments": True,
}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "smartattend-change-this-secret")


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    rows = [dict(r) for r in rows]
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','teacher','student')),
    status TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('pending','approved','rejected')),
    email TEXT,
    phone TEXT,
    department TEXT,
    roll_no TEXT,
    class_name TEXT,
    batch_year TEXT,
    employee_id TEXT,
    designation TEXT,
    verification_note TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    class_name TEXT NOT NULL,
    teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Present','Absent','Leave','Medical')),
    UNIQUE (student_id, subject_id, date)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'Quiz',
    max_marks REAL NOT NULL CHECK (max_marks > 0),
    weightage REAL NOT NULL DEFAULT 0,
    date TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    score REAL,
    absent INTEGER NOT NULL DEFAULT 0,
    UNIQUE (assessment_id, student_id)
);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    due_date TEXT NOT NULL,
    max_marks REAL NOT NULL DEFAULT 10 CHECK (max_marks > 0),
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending','Submitted','Late','Missing')),
    marks REAL,
    feedback TEXT,
    UNIQUE (assignment_id, student_id)
);
"""


def migrate(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='attendance'").fetchone()
    if row and "'Medical'" not in row[0]:
        db.executescript("""
            ALTER TABLE attendance RENAME TO attendance_old;
            CREATE TABLE attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
                date TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('Present','Absent','Leave','Medical')),
                UNIQUE (student_id, subject_id, date)
            );
            INSERT INTO attendance (id, student_id, subject_id, date, status)
                SELECT id, student_id, subject_id, date, status FROM attendance_old;
            DROP TABLE attendance_old;
        """)

    cols = {r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()}
    extra_cols = {
        "status": "TEXT NOT NULL DEFAULT 'approved'",
        "email": "TEXT",
        "phone": "TEXT",
        "department": "TEXT",
        "batch_year": "TEXT",
        "employee_id": "TEXT",
        "designation": "TEXT",
        "verification_note": "TEXT",
        "created_at": "TEXT",
    }
    for col, col_type in extra_cols.items():
        if col not in cols:
            db.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")


def ensure_default_settings(db):
    for k, v in DEFAULT_SETTINGS.items():
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (k, json.dumps(v)),
        )


def get_settings():
    cfg = dict(DEFAULT_SETTINGS)
    for r in query("SELECT key, value FROM settings"):
        if r["key"] in cfg:
            try:
                cfg[r["key"]] = json.loads(r["value"])
            except Exception:
                pass
    try:
        cfg["low_attendance"] = max(1, min(100, int(cfg["low_attendance"])))
    except Exception:
        cfg["low_attendance"] = DEFAULT_LOW_ATTENDANCE
    try:
        cfg["low_marks"] = max(1, min(100, int(cfg["low_marks"])))
    except Exception:
        cfg["low_marks"] = DEFAULT_LOW_MARKS
    return cfg


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    migrate(db)
    ensure_default_settings(db)
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        seed(db)
    seed_academics(db)
    db.commit()
    db.close()


def seed(db):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def add_user(name, username, pw, role, status="approved", **kw):
        return db.execute(
            """INSERT INTO users (
                name, username, password_hash, role, status,
                email, phone, department, roll_no, class_name, batch_year,
                employee_id, designation, verification_note, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name, username, generate_password_hash(pw), role, status,
                kw.get("email"), kw.get("phone"), kw.get("department"),
                kw.get("roll_no"), kw.get("class_name"), kw.get("batch_year"),
                kw.get("employee_id"), kw.get("designation"),
                kw.get("verification_note"), now,
            ),
        ).lastrowid

    add_user("Administrator", "admin", "admin123", "admin", email="admin@college.edu")
    t1 = add_user(
        "Dr. Sharma", "sharma", "teacher123", "teacher",
        email="sharma@college.edu", phone="9876500001", department="CSE",
        employee_id="FAC-101", designation="Associate Professor"
    )
    t2 = add_user(
        "Prof. Singh", "singh", "teacher123", "teacher",
        email="singh@college.edu", phone="9876500002", department="CSE",
        employee_id="FAC-102", designation="Assistant Professor"
    )
    add_user(
        "Ms. Nair", "nair", "teacher123", "teacher", status="pending",
        email="nair.cse@college.edu", phone="9876500099", department="CSE",
        employee_id="FAC-118", designation="Assistant Professor",
        verification_note="Joined CSE dept this semester; assigned to CS204 lab."
    )

    students = [
        ("Aman Verma", "aman", "CSE101"),
        ("Priya Kaur", "priya", "CSE102"),
        ("Rohit Gill", "rohit", "CSE103"),
        ("Simran Dhillon", "simran", "CSE104"),
        ("Karan Mehta", "karan", "CSE105"),
    ]
    sids = [
        add_user(
            n, u, "student123", "student", status="approved",
            email=f"{u}@student.college.edu", phone="9811100000",
            department="CSE", roll_no=r, class_name="CSE-A", batch_year="2023-2027"
        )
        for n, u, r in students
    ]

    add_user(
        "Riya Sharma", "riya", "student123", "student", status="pending",
        email="riya.cse106@student.college.edu", phone="9811100088",
        department="CSE", roll_no="CSE106", class_name="CSE-A", batch_year="2023-2027",
        verification_note="Lateral entry admitted to CSE-A; ID card #2026-884."
    )

    subs = [
        ("Data Structures", "CS201", t1),
        ("Database Systems", "CS202", t1),
        ("Operating Systems", "CS203", t2),
    ]
    sub_ids = [
        db.execute("INSERT INTO subjects (name, code, class_name, teacher_id) VALUES (?,?,?,?)",
                   (n, c, "CSE-A", t)).lastrowid
        for n, c, t in subs
    ]

    d, days = date.today(), []
    while len(days) < 16:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    for si, sid in enumerate(sids):
        for subi, sub in enumerate(sub_ids):
            for di, day in enumerate(days):
                absent = ((di + si * 3 + subi) % 4 == 0) if si != 4 else ((di + subi) % 3 == 0)
                db.execute(
                    "INSERT INTO attendance (student_id, subject_id, date, status) VALUES (?,?,?,?)",
                    (sid, sub, day.isoformat(), "Absent" if absent else "Present"),
                )


def seed_academics(db):
    """Demo marks & assignments for the demo class. Skipped if any exist already
    or if the demo students / subjects are not present."""
    if db.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]:
        return
    stu = db.execute(
        """SELECT id FROM users WHERE role='student' AND status='approved' AND class_name='CSE-A'
           AND username IN ('aman','priya','rohit','simran','karan')
           ORDER BY CASE username WHEN 'aman' THEN 1 WHEN 'priya' THEN 2 WHEN 'rohit' THEN 3
                                  WHEN 'simran' THEN 4 ELSE 5 END""").fetchall()
    subs = db.execute(
        "SELECT id FROM subjects WHERE code IN ('CS201','CS202','CS203') AND class_name='CSE-A' ORDER BY code").fetchall()
    if len(stu) < 5 or len(subs) < 3:
        return
    today = date.today()
    base = [88, 76, 70, 82, 52]
    plan = [("Quiz 1", "Quiz", 20, 10, -30, 1), ("Mid-term", "Mid-term", 50, 30, -14, 1),
            ("Quiz 2", "Quiz", 20, 10, -3, 0)]
    for subi, (sub,) in enumerate(subs):
        for ai, (title, typ, mx, wt, off, pub) in enumerate(plan):
            aid = db.execute(
                "INSERT INTO assessments (subject_id, title, type, max_marks, weightage, date, published) VALUES (?,?,?,?,?,?,?)",
                (sub, title, typ, mx, wt, (today + timedelta(days=off)).isoformat(), pub)).lastrowid
            for si, (sid,) in enumerate(stu):
                pct = max(20, min(100, base[si] + ((subi * 5 + ai * 3 + si) % 9) - 4))
                absent = 1 if (si == 3 and subi == 1 and ai == 0) else 0
                db.execute("INSERT INTO marks (assessment_id, student_id, score, absent) VALUES (?,?,?,?)",
                           (aid, sid, None if absent else round(mx * pct / 100 * 2) / 2, absent))
        for n, (title, off, mx) in enumerate([("Assignment 1", -10, 10), ("Assignment 2", 6, 10)]):
            aid = db.execute(
                "INSERT INTO assignments (subject_id, title, description, due_date, max_marks, created_at) VALUES (?,?,?,?,?,?)",
                (sub, title, "Complete the exercises shared in class and submit before the due date.",
                 (today + timedelta(days=off)).isoformat(), mx, today.isoformat())).lastrowid
            if n == 0:
                for sid_, st, mk, fb in zip([s_[0] for s_ in stu],
                                            ["Submitted", "Submitted", "Late", "Submitted", "Missing"],
                                            [9, 8, 6, 9, None],
                                            ["Well done", "Good work", "Submitted late - marks reduced", "Great effort", "Not submitted"]):
                    db.execute("INSERT INTO submissions (assignment_id, student_id, status, marks, feedback) VALUES (?,?,?,?,?)",
                               (aid, sid_, st, mk, fb))
            else:
                db.execute("INSERT INTO submissions (assignment_id, student_id, status) VALUES (?,?,?)", (aid, stu[0][0], "Submitted"))


# ---------------------------------------------------------------- security & helpers
def role_required(role):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            is_api = request.path.startswith("/api/")
            if "user_id" not in session:
                if is_api:
                    return jsonify(error="Please log in"), 401
                return redirect(url_for("login_page"))
            if session.get("role") != role:
                if is_api:
                    return jsonify(error="Not allowed for your role"), 403
                return redirect(url_for("index"))
            user = query("SELECT status FROM users WHERE id=?", (session["user_id"],), one=True)
            if not user or user["status"] != "approved":
                session.clear()
                if is_api:
                    return jsonify(error="Account is not approved"), 403
                return redirect(url_for("login_page"))
            return fn(*a, **kw)
        return wrapper
    return deco


def bad(msg, code=400):
    return jsonify(error=msg), code


def clean(val):
    return (val or "").strip()


def percent(present, total):
    return round(100 * present / total, 1) if total else 0


def classes_needed(present, total, threshold):
    need = 0
    while total and 100 * present / total < threshold:
        present += 1
        total += 1
        need += 1
    return need


def parse_range():
    start, end = clean(request.args.get("from")) or None, clean(request.args.get("to")) or None
    try:
        if start:
            date.fromisoformat(start)
        if end:
            date.fromisoformat(end)
    except ValueError:
        return None, None, "Invalid date"
    if start and end and start > end:
        return None, None, "'From' date must not be after 'To' date"
    return start, end, None


def build_report(subject_ids, start=None, end=None, only_low=False, threshold=DEFAULT_LOW_ATTENDANCE):
    if not subject_ids:
        return []
    date_sql, args = "", []
    if start:
        date_sql += " AND a.date >= ?"
        args.append(start)
    if end:
        date_sql += " AND a.date <= ?"
        args.append(end)
    marks = ",".join("?" * len(subject_ids))
    rows = query(
        f"""SELECT u.id AS student_id, u.roll_no, u.name, u.class_name,
                   s.id AS subject_id, s.name AS subject, s.code,
                   COALESCE(SUM(a.status='Present'),0) AS present,
                   COALESCE(SUM(a.status='Absent'),0)  AS absent,
                   COALESCE(SUM(a.status='Leave'),0)   AS on_leave,
                   COALESCE(SUM(a.status='Medical'),0) AS medical
            FROM subjects s
            JOIN users u ON u.role='student' AND u.status='approved' AND u.class_name = s.class_name
            LEFT JOIN attendance a ON a.student_id = u.id AND a.subject_id = s.id{date_sql}
            WHERE s.id IN ({marks})
            GROUP BY u.id, s.id
            ORDER BY s.class_name, s.name, u.roll_no""",
        args + list(subject_ids))
    out = []
    for r in rows:
        r["total"] = r["present"] + r["absent"]
        r["percent"] = percent(r["present"], r["total"])
        r["low"] = r["total"] > 0 and r["percent"] < threshold
        r["need"] = classes_needed(r["present"], r["total"], threshold) if r["low"] else 0
        if not only_low or r["low"]:
            out.append(r)
    if only_low:
        out.sort(key=lambda r: r["percent"])
    return out


def safe_cell(v):
    v = "" if v is None else v
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v


def csv_response(rows, start, end, name):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Roll no", "Student", "Class", "Subject code", "Subject",
                "Present", "Absent", "Leave", "Medical", "Classes counted", "Attendance %"])
    for r in rows:
        w.writerow([safe_cell(x) for x in (
            r["roll_no"], r["name"], r["class_name"], r["code"], r["subject"],
            r["present"], r["absent"], r["on_leave"], r["medical"], r["total"], r["percent"])])
    label = f"{start or 'start'}_to_{end or 'today'}"
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}_{label}.csv"'})


def report_response(subject_ids, name):
    cfg = get_settings()
    start, end, err = parse_range()
    if err:
        return bad(err)
    rows = build_report(subject_ids, start, end, only_low=request.args.get("low") == "1", threshold=cfg["low_attendance"])
    if request.args.get("format") == "csv":
        return csv_response(rows, start, end, name)
    return jsonify(rows=rows, threshold=cfg["low_attendance"], start=start, end=end)


# ---------------------------------------------------------------- pages & auth
@app.route("/")
def index():
    role = session.get("role")
    if role:
        return redirect(url_for(f"{role}_page"))
    return redirect(url_for("login_page"))


@app.route("/login", methods=["GET"])
def login_page():
    cfg = get_settings()
    return render_template(
        "login.html",
        allow_student_signup=cfg["allow_student_signup"],
        allow_teacher_signup=cfg["allow_teacher_signup"],
    )


@app.route("/api/signup", methods=["POST"])
def api_signup():
    cfg = get_settings()
    d = request.get_json(silent=True) or {}
    role = clean(d.get("role")).lower()
    name = clean(d.get("name"))
    email = clean(d.get("email")).lower()
    phone = clean(d.get("phone"))
    department = clean(d.get("department"))
    username = clean(d.get("username")).lower()
    pw = d.get("password") or ""
    note = clean(d.get("verification_note"))

    if role not in ("student", "teacher"):
        return bad("Choose a valid account type (student or teacher)")
    if role == "student" and not cfg["allow_student_signup"]:
        return bad("Student self-registration is currently disabled by admin", 403)
    if role == "teacher" and not cfg["allow_teacher_signup"]:
        return bad("Teacher self-registration is currently disabled by admin", 403)
    if not (name and email and phone and department and username and pw):
        return bad("Full name, email, phone, department, username, and password are required")
    if "@" not in email or "." not in email:
        return bad("Enter a valid email address")
    if len(pw) < 6:
        return bad("Password must be at least 6 characters")

    roll, cls, batch, emp_id, designation = None, None, None, None, None
    if role == "student":
        roll = clean(d.get("roll_no"))
        cls = clean(d.get("class_name"))
        batch = clean(d.get("batch_year"))
        if not (roll and cls and batch):
            return bad("Roll number, class/section, and batch year are required for students")
    else:
        emp_id = clean(d.get("employee_id"))
        designation = clean(d.get("designation"))
        if not (emp_id and designation):
            return bad("Employee/Faculty ID and designation are required for teachers")

    if query("SELECT 1 FROM users WHERE username=?", (username,), one=True):
        return bad("Username already exists")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    execute(
        """INSERT INTO users (
            name, username, password_hash, role, status,
            email, phone, department, roll_no, class_name, batch_year,
            employee_id, designation, verification_note, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            name, username, generate_password_hash(pw), role, "pending",
            email, phone, department, roll, cls, batch,
            emp_id, designation, note, now,
        ),
    )
    return jsonify(ok=True, message="Registration submitted with verification details. Wait for admin approval."), 201


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    user = query("SELECT * FROM users WHERE username = ?", (clean(data.get("username")).lower(),), one=True)
    if not user or not check_password_hash(user["password_hash"], data.get("password") or ""):
        return bad("Invalid username or password", 401)
    if user["status"] == "pending":
        return bad("Your account is pending admin verification. Please wait for approval.", 403)
    if user["status"] == "rejected":
        return bad("Your account request was rejected by admin.", 403)
    session.clear()
    session.update(user_id=user["id"], role=user["role"], name=user["name"])
    return jsonify(role=user["role"], redirect=url_for(f"{user['role']}_page"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/admin")
@role_required("admin")
def admin_page():
    return render_template("admin.html", name=session["name"])


@app.route("/teacher")
@role_required("teacher")
def teacher_page():
    return render_template("teacher.html", name=session["name"])


@app.route("/student")
@role_required("student")
def student_page():
    return render_template("student.html", name=session["name"])


@app.route("/api/change-password", methods=["POST"])
def api_change_password():
    if "user_id" not in session:
        return bad("Please log in", 401)
    d = request.get_json(silent=True) or {}
    current, new = d.get("current_password") or "", d.get("new_password") or ""
    user = query("SELECT password_hash FROM users WHERE id=?", (session["user_id"],), one=True)
    if not user or not check_password_hash(user["password_hash"], current):
        return bad("Current password is incorrect")
    if len(new) < 6:
        return bad("New password must be at least 6 characters")
    if new == current:
        return bad("New password must be different from the current one")
    execute("UPDATE users SET password_hash=? WHERE id=?",
            (generate_password_hash(new), session["user_id"]))
    return jsonify(ok=True)


# ---------------------------------------------------------------- admin API
@app.route("/api/admin/stats")
@role_required("admin")
def admin_stats():
    count = lambda role, st="approved": query(
        "SELECT COUNT(*) c FROM users WHERE role=? AND status=?", (role, st), one=True
    )["c"]
    per_subject = query(
        """SELECT s.name, COALESCE(SUM(a.status IN ('Present','Absent')),0) total,
                  COALESCE(SUM(a.status='Present'),0) present
           FROM subjects s LEFT JOIN attendance a ON a.subject_id = s.id GROUP BY s.id ORDER BY s.name"""
    )
    for r in per_subject:
        r["percent"] = round(100 * r["present"] / r["total"], 1) if r["total"] else 0
    tot = sum(r["total"] for r in per_subject)
    pres = sum(r["present"] for r in per_subject)
    pending_count = query("SELECT COUNT(*) c FROM users WHERE status='pending'", one=True)["c"]
    return jsonify(
        students=count("student", "approved"),
        teachers=count("teacher", "approved"),
        pending=pending_count,
        subjects=query("SELECT COUNT(*) c FROM subjects", one=True)["c"],
        overall=round(100 * pres / tot, 1) if tot else 0,
        per_subject=per_subject,
    )


@app.route("/api/admin/pending-users")
@role_required("admin")
def admin_pending_users():
    return jsonify(query(
        """SELECT id, name, username, role, status, email, phone, department,
                  roll_no, class_name, batch_year, employee_id, designation,
                  verification_note, created_at
           FROM users WHERE status='pending' ORDER BY id DESC"""
    ))


@app.route("/api/admin/users/<int:uid>/status", methods=["POST"])
@role_required("admin")
def admin_user_status(uid):
    d = request.get_json(silent=True) or {}
    new_status = clean(d.get("status")).lower()
    if new_status not in ("approved", "rejected", "pending"):
        return bad("Invalid status")
    u = query("SELECT role FROM users WHERE id=?", (uid,), one=True)
    if not u or u["role"] == "admin":
        return bad("User not found", 404)
    execute("UPDATE users SET status=? WHERE id=?", (new_status, uid))
    return jsonify(ok=True)


@app.route("/api/admin/students", methods=["GET", "POST"])
@role_required("admin")
def admin_students():
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        name, username, pw = clean(d.get("name")), clean(d.get("username")).lower(), d.get("password") or ""
        roll, cls = clean(d.get("roll_no")), clean(d.get("class_name"))
        email, phone = clean(d.get("email")), clean(d.get("phone"))
        dept, batch = clean(d.get("department")), clean(d.get("batch_year"))
        if not (name and username and pw and roll and cls):
            return bad("Name, roll no., class, username, and password are required")
        if len(pw) < 6:
            return bad("Password must be at least 6 characters")
        if query("SELECT 1 FROM users WHERE username=?", (username,), one=True):
            return bad("Username already exists")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        execute(
            """INSERT INTO users (name, username, password_hash, role, status, email, phone, department, roll_no, class_name, batch_year, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, username, generate_password_hash(pw), "student", "approved", email, phone, dept, roll, cls, batch, now),
        )
        return jsonify(ok=True), 201
    return jsonify(query(
        """SELECT id, name, username, status, email, phone, department, roll_no, class_name, batch_year
           FROM users WHERE role='student' AND status='approved' ORDER BY class_name, roll_no"""
    ))


@app.route("/api/admin/teachers", methods=["GET", "POST"])
@role_required("admin")
def admin_teachers():
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        name, username, pw = clean(d.get("name")), clean(d.get("username")).lower(), d.get("password") or ""
        email, phone = clean(d.get("email")), clean(d.get("phone"))
        dept, emp_id, desig = clean(d.get("department")), clean(d.get("employee_id")), clean(d.get("designation"))
        if not (name and username and pw):
            return bad("Name, username, and password are required")
        if len(pw) < 6:
            return bad("Password must be at least 6 characters")
        if query("SELECT 1 FROM users WHERE username=?", (username,), one=True):
            return bad("Username already exists")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        execute(
            """INSERT INTO users (name, username, password_hash, role, status, email, phone, department, employee_id, designation, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (name, username, generate_password_hash(pw), "teacher", "approved", email, phone, dept, emp_id, desig, now),
        )
        return jsonify(ok=True), 201
    return jsonify(query(
        """SELECT id, name, username, status, email, phone, department, employee_id, designation
           FROM users WHERE role='teacher' AND status='approved' ORDER BY name"""
    ))


@app.route("/api/admin/users/<int:uid>", methods=["DELETE"])
@role_required("admin")
def admin_delete_user(uid):
    u = query("SELECT role FROM users WHERE id=?", (uid,), one=True)
    if not u or u["role"] == "admin":
        return bad("User not found", 404)
    execute("DELETE FROM users WHERE id=?", (uid,))
    return jsonify(ok=True)


@app.route("/api/admin/subjects", methods=["GET", "POST"])
@role_required("admin")
def admin_subjects():
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        name, code, cls = clean(d.get("name")), clean(d.get("code")), clean(d.get("class_name"))
        tid = d.get("teacher_id")
        if not (name and code and cls and tid):
            return bad("All fields are required")
        if not query("SELECT 1 FROM users WHERE id=? AND role='teacher' AND status='approved'", (tid,), one=True):
            return bad("Choose an approved teacher")
        execute("INSERT INTO subjects (name, code, class_name, teacher_id) VALUES (?,?,?,?)",
                (name, code, cls, tid))
        return jsonify(ok=True), 201
    return jsonify(query(
        """SELECT s.id, s.name, s.code, s.class_name, u.name AS teacher
           FROM subjects s LEFT JOIN users u ON u.id = s.teacher_id ORDER BY s.class_name, s.name"""))


@app.route("/api/admin/subjects/<int:sid>", methods=["DELETE"])
@role_required("admin")
def admin_delete_subject(sid):
    execute("DELETE FROM subjects WHERE id=?", (sid,))
    return jsonify(ok=True)


@app.route("/api/admin/report")
@role_required("admin")
def admin_report():
    sid = request.args.get("subject_id", type=int)
    if sid:
        if not query("SELECT 1 FROM subjects WHERE id=?", (sid,), one=True):
            return bad("Subject not found", 404)
        ids = [sid]
    else:
        ids = [r["id"] for r in query("SELECT id FROM subjects")]
    return report_response(ids, "attendance_report")


@app.route("/api/admin/settings", methods=["GET", "POST"])
@role_required("admin")
def admin_settings():
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        try:
            low = int(d.get("low_attendance", DEFAULT_LOW_ATTENDANCE))
        except Exception:
            return bad("Minimum attendance percentage must be a number")
        if low < 1 or low > 100:
            return bad("Minimum attendance percentage must be between 1 and 100")
        try:
            low_marks = int(d.get("low_marks", DEFAULT_LOW_MARKS))
        except Exception:
            return bad("Minimum marks percentage must be a number")
        if low_marks < 1 or low_marks > 100:
            return bad("Minimum marks percentage must be between 1 and 100")

        updated = {
            "low_attendance": low,
            "low_marks": low_marks,
            "allow_student_signup": bool(d.get("allow_student_signup")),
            "allow_teacher_signup": bool(d.get("allow_teacher_signup")),
            "teacher_can_mark": bool(d.get("teacher_can_mark")),
            "teacher_can_edit": bool(d.get("teacher_can_edit")),
            "teacher_can_reports": bool(d.get("teacher_can_reports")),
            "teacher_allow_future_dates": bool(d.get("teacher_allow_future_dates")),
            "student_can_view_history": bool(d.get("student_can_view_history")),
            "student_can_view_trend": bool(d.get("student_can_view_trend")),
            "student_can_view_alerts": bool(d.get("student_can_view_alerts")),
            "teacher_can_manage_academics": bool(d.get("teacher_can_manage_academics")),
            "student_can_view_marks": bool(d.get("student_can_view_marks")),
            "student_can_view_class_avg": bool(d.get("student_can_view_class_avg")),
            "student_can_view_assignments": bool(d.get("student_can_view_assignments")),
        }
        db = get_db()
        for k, v in updated.items():
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, json.dumps(v)),
            )
        db.commit()
        return jsonify(ok=True, settings=get_settings())

    return jsonify(get_settings())


# ---------------------------------------------------------------- teacher API
def own_subject(subject_id):
    return query("SELECT * FROM subjects WHERE id=? AND teacher_id=?",
                 (subject_id, session["user_id"]), one=True)


@app.route("/api/teacher/subjects")
@role_required("teacher")
def teacher_subjects():
    cfg = get_settings()
    subs = query("SELECT id, name, code, class_name FROM subjects WHERE teacher_id=? ORDER BY name",
                 (session["user_id"],))
    return jsonify(subjects=subs, permissions=cfg)


@app.route("/api/teacher/students")
@role_required("teacher")
def teacher_students():
    cfg = get_settings()
    if not cfg["teacher_can_mark"] and not cfg["teacher_can_edit"]:
        return bad("Attendance marking and editing are currently disabled by admin", 403)
    subject_id = request.args.get("subject_id", type=int)
    day = request.args.get("date") or date.today().isoformat()
    sub = own_subject(subject_id)
    if not sub:
        return bad("Subject not found", 404)
    rows = query(
        """SELECT u.id, u.name, u.roll_no, a.status,
             (SELECT COUNT(*) FROM attendance x
                WHERE x.student_id = u.id AND x.subject_id = ? AND x.status = 'Present') AS present,
             (SELECT COUNT(*) FROM attendance x
                WHERE x.student_id = u.id AND x.subject_id = ? AND x.status IN ('Present','Absent')) AS total,
             (SELECT COUNT(*) FROM attendance x
                WHERE x.student_id = u.id AND x.subject_id = ? AND x.status IN ('Leave','Medical')) AS excused
           FROM users u LEFT JOIN attendance a
             ON a.student_id = u.id AND a.subject_id = ? AND a.date = ?
           WHERE u.role='student' AND u.status='approved' AND u.class_name = ? ORDER BY u.roll_no""",
        (subject_id, subject_id, subject_id, subject_id, day, sub["class_name"]))
    for r in rows:
        r["absent"] = r["total"] - r["present"]
    already_marked = any(r["status"] for r in rows)
    can_save = (cfg["teacher_can_edit"] if already_marked else cfg["teacher_can_mark"])
    return jsonify(students=rows, marked=already_marked, can_save=can_save)


@app.route("/api/teacher/attendance", methods=["POST"])
@role_required("teacher")
def teacher_mark():
    cfg = get_settings()
    d = request.get_json(silent=True) or {}
    day = clean(d.get("date"))
    try:
        parsed_day = date.fromisoformat(day)
    except ValueError:
        return bad("Invalid date")
    if not cfg["teacher_allow_future_dates"] and parsed_day > date.today():
        return bad("Future dates are disabled by admin")
    sub = own_subject(d.get("subject_id"))
    if not sub:
        return bad("Subject not found", 404)
    valid = {r["id"] for r in query("SELECT id FROM users WHERE role='student' AND status='approved' AND class_name=?", (sub["class_name"],))}
    db = get_db()
    n = 0
    for rec in d.get("records") or []:
        sid = rec.get("student_id")
        st = rec.get("status")
        if sid in valid and st in STATUSES:
            existing = query(
                "SELECT 1 FROM attendance WHERE student_id=? AND subject_id=? AND date=?",
                (sid, sub["id"], day), one=True
            )
            if existing and not cfg["teacher_can_edit"]:
                return bad("Editing already-marked attendance is disabled by admin", 403)
            if not existing and not cfg["teacher_can_mark"]:
                return bad("Marking new attendance is disabled by admin", 403)
            db.execute(
                """INSERT INTO attendance (student_id, subject_id, date, status) VALUES (?,?,?,?)
                   ON CONFLICT(student_id, subject_id, date) DO UPDATE SET status = excluded.status""",
                (sid, sub["id"], day, st))
            n += 1
    db.commit()
    return jsonify(ok=True, saved=n)


@app.route("/api/teacher/report")
@role_required("teacher")
def teacher_report():
    cfg = get_settings()
    if not cfg["teacher_can_reports"]:
        return bad("Attendance reports are currently disabled by admin", 403)
    sid = request.args.get("subject_id", type=int)
    if sid:
        if not own_subject(sid):
            return bad("Subject not found", 404)
        ids = [sid]
    else:
        ids = [r["id"] for r in query("SELECT id FROM subjects WHERE teacher_id=?", (session["user_id"],))]
    return report_response(ids, "attendance_report")


# ---------------------------------------------------------------- student API
@app.route("/api/student/summary")
@role_required("student")
def student_summary():
    cfg = get_settings()
    threshold = cfg["low_attendance"]
    me = query("SELECT name, roll_no, class_name FROM users WHERE id=?", (session["user_id"],), one=True)
    rows = query(
        """SELECT s.id, s.name, s.code,
                  COALESCE(SUM(a.status IN ('Present','Absent')),0) total,
                  COALESCE(SUM(a.status='Present'),0) present,
                  COALESCE(SUM(a.status IN ('Leave','Medical')),0) excused
           FROM subjects s LEFT JOIN attendance a ON a.subject_id = s.id AND a.student_id = ?
           WHERE s.class_name = ? GROUP BY s.id ORDER BY s.name""",
        (session["user_id"], me["class_name"]))
    for r in rows:
        r["percent"] = percent(r["present"], r["total"])
        r["low"] = r["total"] > 0 and r["percent"] < threshold
        r["need"] = classes_needed(r["present"], r["total"], threshold) if r["low"] else 0
    tot, pres = sum(r["total"] for r in rows), sum(r["present"] for r in rows)
    return jsonify(
        student=me,
        subjects=rows,
        threshold=threshold,
        overall=percent(pres, tot),
        permissions=cfg,
    )


@app.route("/api/student/history")
@role_required("student")
def student_history():
    cfg = get_settings()
    if not cfg["student_can_view_history"]:
        return bad("Attendance history viewing is currently disabled by admin", 403)
    subject_id = request.args.get("subject_id", type=int)
    sql = """SELECT a.date, a.status, s.name AS subject FROM attendance a
             JOIN subjects s ON s.id = a.subject_id WHERE a.student_id = ?"""
    args = [session["user_id"]]
    if subject_id:
        sql += " AND a.subject_id = ?"
        args.append(subject_id)
    sql += " ORDER BY a.date DESC, s.name LIMIT 200"
    return jsonify(query(sql, args))


@app.route("/api/student/trend")
@role_required("student")
def student_trend():
    cfg = get_settings()
    if not cfg["student_can_view_trend"]:
        return bad("Attendance trend chart is currently disabled by admin", 403)
    fmt = "%Y-W%W" if request.args.get("by") == "week" else "%Y-%m"
    rows = query(
        f"""SELECT strftime('{fmt}', date) AS period,
                   SUM(status='Present') AS present,
                   SUM(status IN ('Present','Absent')) AS total
            FROM attendance WHERE student_id = ? AND status IN ('Present','Absent')
            GROUP BY period ORDER BY period""",
        (session["user_id"],))
    for r in rows:
        r["percent"] = percent(r["present"], r["total"])
    return jsonify(rows)


# ---------------------------------------------------------------- marks & assignments
ASSESSMENT_TYPES = ("Quiz", "Mid-term", "Final", "Practical", "Project")
ASSIGN_STATUSES = ("Pending", "Submitted", "Late", "Missing")


def grade_for(p):
    for cut, g in ((90, "A+"), (80, "A"), (70, "B"), (60, "C"), (50, "D")):
        if p >= cut:
            return g
    return "F"


def to_num(v):
    """'' / None -> None, otherwise a finite float (ValueError if invalid)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        raise ValueError("bad number")
    n = float(v)
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError("bad number")
    return n


def require_manage():
    if not get_settings()["teacher_can_manage_academics"]:
        return bad("Marks and assignments are currently disabled by admin", 403)
    return None


def own_assessment(aid):
    return query(
        """SELECT a.*, s.class_name, s.name AS subject FROM assessments a
           JOIN subjects s ON s.id = a.subject_id WHERE a.id=? AND s.teacher_id=?""",
        (aid, session["user_id"]), one=True)


def own_assignment(aid):
    return query(
        """SELECT a.*, s.class_name, s.name AS subject FROM assignments a
           JOIN subjects s ON s.id = a.subject_id WHERE a.id=? AND s.teacher_id=?""",
        (aid, session["user_id"]), one=True)


def class_student_ids(class_name):
    return {r["id"] for r in query(
        "SELECT id FROM users WHERE role='student' AND status='approved' AND class_name=?", (class_name,))}


@app.route("/api/teacher/assessments", methods=["GET", "POST"])
@role_required("teacher")
def teacher_assessments():
    err = require_manage()
    if err:
        return err
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        try:
            sid = int(d.get("subject_id"))
            max_marks = to_num(d.get("max_marks"))
            weight = to_num(d.get("weightage"))
        except (TypeError, ValueError):
            return bad("Subject, maximum marks and weightage must be valid numbers")
        title, typ = clean(d.get("title")), clean(d.get("type")) or "Quiz"
        day = clean(d.get("date")) or date.today().isoformat()
        try:
            date.fromisoformat(day)
        except ValueError:
            return bad("Invalid date")
        sub = own_subject(sid)
        if not sub:
            return bad("Subject not found", 404)
        if not title:
            return bad("Title is required")
        if typ not in ASSESSMENT_TYPES:
            return bad("Invalid assessment type")
        if max_marks is None or max_marks <= 0:
            return bad("Maximum marks must be greater than 0")
        weight = 0 if weight is None else weight
        if not 0 <= weight <= 100:
            return bad("Weightage must be between 0 and 100")
        new_id = execute(
            "INSERT INTO assessments (subject_id, title, type, max_marks, weightage, date, published) VALUES (?,?,?,?,?,?,0)",
            (sid, title, typ, max_marks, weight, day))
        return jsonify(ok=True, id=new_id), 201
    rows = query(
        """SELECT a.id, a.title, a.type, a.max_marks, a.weightage, a.date, a.published,
                  s.id AS subject_id, s.name AS subject, s.class_name,
                  (SELECT COUNT(*) FROM marks m WHERE m.assessment_id = a.id) AS entered,
                  (SELECT COUNT(*) FROM users u WHERE u.role='student' AND u.status='approved'
                      AND u.class_name = s.class_name) AS students,
                  (SELECT AVG(CASE WHEN m.absent=1 THEN 0 ELSE m.score END) FROM marks m
                      WHERE m.assessment_id = a.id AND (m.absent=1 OR m.score IS NOT NULL)) AS avg
           FROM assessments a JOIN subjects s ON s.id = a.subject_id
           WHERE s.teacher_id=? ORDER BY a.date DESC, a.id DESC""", (session["user_id"],))
    for r in rows:
        r["avg_percent"] = round(100 * r["avg"] / r["max_marks"], 1) if r["avg"] is not None else None
        del r["avg"]
    return jsonify(rows)


@app.route("/api/teacher/assessments/<int:aid>", methods=["DELETE"])
@role_required("teacher")
def teacher_delete_assessment(aid):
    err = require_manage()
    if err:
        return err
    if not own_assessment(aid):
        return bad("Assessment not found", 404)
    execute("DELETE FROM assessments WHERE id=?", (aid,))
    return jsonify(ok=True)


@app.route("/api/teacher/assessments/<int:aid>/publish", methods=["POST"])
@role_required("teacher")
def teacher_publish_assessment(aid):
    err = require_manage()
    if err:
        return err
    if not own_assessment(aid):
        return bad("Assessment not found", 404)
    d = request.get_json(silent=True) or {}
    execute("UPDATE assessments SET published=? WHERE id=?", (1 if d.get("published") else 0, aid))
    return jsonify(ok=True)


@app.route("/api/teacher/assessments/<int:aid>/marks", methods=["GET", "POST"])
@role_required("teacher")
def teacher_assessment_marks(aid):
    err = require_manage()
    if err:
        return err
    a = own_assessment(aid)
    if not a:
        return bad("Assessment not found", 404)
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        valid = class_student_ids(a["class_name"])
        todo = []
        for rec in d.get("records") or []:
            sid = rec.get("student_id")
            if sid not in valid:
                continue
            absent = bool(rec.get("absent"))
            try:
                score = None if absent else to_num(rec.get("score"))
            except (TypeError, ValueError):
                return bad("Marks must be valid numbers")
            if score is not None and not 0 <= score <= a["max_marks"]:
                return bad(f"Marks must be between 0 and {a['max_marks']:g}")
            todo.append((sid, score, absent))
        db = get_db()
        for sid, score, absent in todo:
            if score is None and not absent:
                db.execute("DELETE FROM marks WHERE assessment_id=? AND student_id=?", (aid, sid))
            else:
                db.execute(
                    """INSERT INTO marks (assessment_id, student_id, score, absent) VALUES (?,?,?,?)
                       ON CONFLICT(assessment_id, student_id)
                       DO UPDATE SET score = excluded.score, absent = excluded.absent""",
                    (aid, sid, score, 1 if absent else 0))
        db.commit()
        return jsonify(ok=True, saved=len(todo))
    rows = query(
        """SELECT u.id, u.name, u.roll_no, m.score, COALESCE(m.absent, 0) AS absent
           FROM users u LEFT JOIN marks m ON m.student_id = u.id AND m.assessment_id = ?
           WHERE u.role='student' AND u.status='approved' AND u.class_name = ? ORDER BY u.roll_no""",
        (aid, a["class_name"]))
    return jsonify(assessment=a, students=rows)


@app.route("/api/teacher/assignments", methods=["GET", "POST"])
@role_required("teacher")
def teacher_assignments():
    err = require_manage()
    if err:
        return err
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        try:
            sid = int(d.get("subject_id"))
            max_marks = to_num(d.get("max_marks"))
        except (TypeError, ValueError):
            return bad("Subject and maximum marks must be valid numbers")
        title, desc = clean(d.get("title")), clean(d.get("description"))
        due = clean(d.get("due_date"))
        try:
            date.fromisoformat(due)
        except ValueError:
            return bad("Choose a valid due date")
        if not own_subject(sid):
            return bad("Subject not found", 404)
        if not title:
            return bad("Title is required")
        max_marks = 10 if max_marks is None else max_marks
        if max_marks <= 0:
            return bad("Maximum marks must be greater than 0")
        new_id = execute(
            "INSERT INTO assignments (subject_id, title, description, due_date, max_marks, created_at) VALUES (?,?,?,?,?,?)",
            (sid, title, desc[:500], due, max_marks, date.today().isoformat()))
        return jsonify(ok=True, id=new_id), 201
    rows = query(
        """SELECT a.id, a.title, a.description, a.due_date, a.max_marks,
                  s.id AS subject_id, s.name AS subject, s.class_name,
                  (SELECT COUNT(*) FROM submissions x WHERE x.assignment_id = a.id
                      AND x.status IN ('Submitted','Late')) AS submitted,
                  (SELECT COUNT(*) FROM users u WHERE u.role='student' AND u.status='approved'
                      AND u.class_name = s.class_name) AS students
           FROM assignments a JOIN subjects s ON s.id = a.subject_id
           WHERE s.teacher_id=? ORDER BY a.due_date DESC, a.id DESC""", (session["user_id"],))
    return jsonify(rows)


@app.route("/api/teacher/assignments/<int:aid>", methods=["DELETE"])
@role_required("teacher")
def teacher_delete_assignment(aid):
    err = require_manage()
    if err:
        return err
    if not own_assignment(aid):
        return bad("Assignment not found", 404)
    execute("DELETE FROM assignments WHERE id=?", (aid,))
    return jsonify(ok=True)


@app.route("/api/teacher/assignments/<int:aid>/submissions", methods=["GET", "POST"])
@role_required("teacher")
def teacher_submissions(aid):
    err = require_manage()
    if err:
        return err
    a = own_assignment(aid)
    if not a:
        return bad("Assignment not found", 404)
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        valid = class_student_ids(a["class_name"])
        todo = []
        for rec in d.get("records") or []:
            sid = rec.get("student_id")
            if sid not in valid:
                continue
            status = rec.get("status")
            if status not in ASSIGN_STATUSES:
                return bad("Invalid submission status")
            try:
                mk = to_num(rec.get("marks"))
            except (TypeError, ValueError):
                return bad("Marks must be valid numbers")
            if mk is not None and not 0 <= mk <= a["max_marks"]:
                return bad(f"Marks must be between 0 and {a['max_marks']:g}")
            todo.append((sid, status, mk, clean(rec.get("feedback"))[:500]))
        db = get_db()
        for sid, status, mk, fb in todo:
            db.execute(
                """INSERT INTO submissions (assignment_id, student_id, status, marks, feedback) VALUES (?,?,?,?,?)
                   ON CONFLICT(assignment_id, student_id)
                   DO UPDATE SET status = excluded.status, marks = excluded.marks, feedback = excluded.feedback""",
                (aid, sid, status, mk, fb))
        db.commit()
        return jsonify(ok=True, saved=len(todo))
    rows = query(
        """SELECT u.id, u.name, u.roll_no, COALESCE(x.status, 'Pending') AS status,
                  x.marks, COALESCE(x.feedback, '') AS feedback
           FROM users u LEFT JOIN submissions x ON x.student_id = u.id AND x.assignment_id = ?
           WHERE u.role='student' AND u.status='approved' AND u.class_name = ? ORDER BY u.roll_no""",
        (aid, a["class_name"]))
    return jsonify(assignment=a, students=rows)


@app.route("/api/student/marks")
@role_required("student")
def student_marks():
    cfg = get_settings()
    if not cfg["student_can_view_marks"]:
        return bad("Viewing marks is currently disabled by admin", 403)
    me = query("SELECT class_name FROM users WHERE id=?", (session["user_id"],), one=True)
    rows = query(
        """SELECT a.id, a.title, a.type, a.max_marks, a.weightage, a.date,
                  s.id AS subject_id, s.name AS subject, s.code, m.score, m.absent,
                  (SELECT AVG(CASE WHEN x.absent=1 THEN 0 ELSE x.score END) FROM marks x
                      WHERE x.assessment_id = a.id AND (x.absent=1 OR x.score IS NOT NULL)) AS class_avg
           FROM assessments a JOIN subjects s ON s.id = a.subject_id
           LEFT JOIN marks m ON m.assessment_id = a.id AND m.student_id = ?
           WHERE s.class_name = ? AND a.published = 1
           ORDER BY a.date DESC, a.id DESC""", (session["user_id"], me["class_name"]))
    groups = {}
    for r in rows:
        r["is_absent"] = r["absent"] == 1
        r["has_result"] = r["is_absent"] or r["score"] is not None
        if r["is_absent"]:
            r["score"] = 0
        r["percent"] = round(100 * r["score"] / r["max_marks"], 1) if r["has_result"] else None
        r["grade"] = grade_for(r["percent"]) if r["has_result"] else None
        show_avg = cfg["student_can_view_class_avg"] and r["class_avg"] is not None
        r["class_avg_percent"] = round(100 * r["class_avg"] / r["max_marks"], 1) if show_avg else None
        for k in ("class_avg", "absent"):
            del r[k]
        g = groups.setdefault(r["subject_id"], {"id": r["subject_id"], "name": r["subject"], "code": r["code"], "items": []})
        if r["has_result"]:
            g["items"].append((r["percent"], r["weightage"]))
    subjects = []
    for g in groups.values():
        items = g.pop("items")
        wsum = sum(w for _, w in items)
        if items:
            pct = sum(p * w for p, w in items) / wsum if wsum > 0 else sum(p for p, _ in items) / len(items)
            g["percent"], g["grade"] = round(pct, 1), grade_for(pct)
        else:
            g["percent"], g["grade"] = None, None
        g["count"] = len(items)
        subjects.append(g)
    scored = [g["percent"] for g in subjects if g["percent"] is not None]
    overall = round(sum(scored) / len(scored), 1) if scored else None
    return jsonify(subjects=subjects, results=rows, overall=overall,
                   grade=grade_for(overall) if overall is not None else None)


@app.route("/api/student/assignments")
@role_required("student")
def student_assignments():
    cfg = get_settings()
    if not cfg["student_can_view_assignments"]:
        return bad("Viewing assignments is currently disabled by admin", 403)
    me = query("SELECT class_name FROM users WHERE id=?", (session["user_id"],), one=True)
    rows = query(
        """SELECT a.id, a.title, a.description, a.due_date, a.max_marks, s.name AS subject,
                  COALESCE(x.status, 'Pending') AS status, x.marks, COALESCE(x.feedback, '') AS feedback
           FROM assignments a JOIN subjects s ON s.id = a.subject_id
           LEFT JOIN submissions x ON x.assignment_id = a.id AND x.student_id = ?
           WHERE s.class_name = ? ORDER BY a.due_date DESC, a.id DESC""", (session["user_id"], me["class_name"]))
    today = date.today().isoformat()
    for r in rows:
        r["overdue"] = r["status"] == "Pending" and r["due_date"] < today
    summary = {
        "total": len(rows),
        "done": sum(r["status"] in ("Submitted", "Late") for r in rows),
        "pending": sum(r["status"] == "Pending" for r in rows),
        "overdue": sum(r["overdue"] for r in rows),
        "missing": sum(r["status"] == "Missing" for r in rows),
    }
    return jsonify(assignments=rows, summary=summary)


# ---------------------------------------------------------------- standing, at-risk & report card
def weighted_percent(items):
    """items: [(percent, weight)] -> weighted average (plain average if all weights are 0)."""
    if not items:
        return None
    wsum = sum(w for _, w in items)
    pct = sum(p * w for p, w in items) / wsum if wsum > 0 else sum(p for p, _ in items) / len(items)
    return round(pct, 1)


def student_standing(student_id, cfg, subject_ids=None, include_marks=True, include_asg=True):
    """One student's attendance + marks + assignments picture and risk flags.
    Only published marks are used. subject_ids limits the view (e.g. to one teacher's subjects)."""
    me = query("""SELECT id, name, roll_no, class_name, department, batch_year, email
                  FROM users WHERE id=? AND role='student'""", (student_id,), one=True)
    if not me:
        return None
    subs = query("SELECT id, name, code FROM subjects WHERE class_name=? ORDER BY name", (me["class_name"],))
    if subject_ids is not None:
        allowed = set(subject_ids)
        subs = [s for s in subs if s["id"] in allowed]
    thr, low_marks = cfg["low_attendance"], cfg["low_marks"]
    today = date.today().isoformat()
    by = {s["id"]: {**s, "att_present": 0, "att_total": 0, "att_excused": 0, "att_percent": None, "att_low": False,
                    "assessments": [], "marks_percent": None, "marks_grade": None, "marks_low": False,
                    "assignments": [], "asg_done": 0, "asg_missing": 0, "asg_overdue": 0, "asg_due_total": 0}
          for s in subs}
    ids = list(by)
    if ids:
        qm = ",".join("?" * len(ids))
        for r in query(
                f"""SELECT subject_id, COALESCE(SUM(status='Present'),0) p,
                           COALESCE(SUM(status IN ('Present','Absent')),0) t,
                           COALESCE(SUM(status IN ('Leave','Medical')),0) e
                    FROM attendance WHERE student_id=? AND subject_id IN ({qm}) GROUP BY subject_id""",
                [student_id] + ids):
            b = by[r["subject_id"]]
            b["att_present"], b["att_total"], b["att_excused"] = r["p"], r["t"], r["e"]
        if include_marks:
            for r in query(
                    f"""SELECT a.subject_id, a.title, a.type, a.max_marks, a.weightage, a.date, m.score, m.absent
                        FROM assessments a LEFT JOIN marks m ON m.assessment_id = a.id AND m.student_id = ?
                        WHERE a.published = 1 AND a.subject_id IN ({qm}) ORDER BY a.date, a.id""",
                    [student_id] + ids):
                absent = r["absent"] == 1
                has = absent or r["score"] is not None
                score = 0 if absent else r["score"]
                pct = round(100 * score / r["max_marks"], 1) if has else None
                by[r["subject_id"]]["assessments"].append({
                    "title": r["title"], "type": r["type"], "date": r["date"], "max_marks": r["max_marks"],
                    "weightage": r["weightage"], "score": score, "absent": absent, "has_result": has,
                    "percent": pct, "grade": grade_for(pct) if has else None})
        if include_asg:
            for r in query(
                    f"""SELECT a.subject_id, a.title, a.due_date, a.max_marks,
                               COALESCE(x.status,'Pending') AS status, x.marks
                        FROM assignments a LEFT JOIN submissions x ON x.assignment_id = a.id AND x.student_id = ?
                        WHERE a.subject_id IN ({qm}) ORDER BY a.due_date""", [student_id] + ids):
                r["overdue"] = r["status"] == "Pending" and r["due_date"] < today
                by[r["subject_id"]]["assignments"].append(r)

    subjects = list(by.values())
    for b in subjects:
        if b["att_total"]:
            b["att_percent"] = percent(b["att_present"], b["att_total"])
            b["att_low"] = b["att_percent"] < thr
        b["marks_percent"] = weighted_percent([(a["percent"], a["weightage"]) for a in b["assessments"] if a["has_result"]])
        if b["marks_percent"] is not None:
            b["marks_grade"] = grade_for(b["marks_percent"])
            b["marks_low"] = b["marks_percent"] < low_marks
        b["asg_done"] = sum(a["status"] in ("Submitted", "Late") for a in b["assignments"])
        b["asg_missing"] = sum(a["status"] == "Missing" for a in b["assignments"])
        b["asg_overdue"] = sum(a["overdue"] for a in b["assignments"])
        b["asg_due_total"] = b["asg_done"] + b["asg_missing"] + b["asg_overdue"]

    # ---- overall numbers + traffic-light status
    pres, tot = sum(b["att_present"] for b in subjects), sum(b["att_total"] for b in subjects)
    att_pct = percent(pres, tot) if tot else None
    att_status = None if att_pct is None else "good" if att_pct >= min(100, thr + 10) else "warn" if att_pct >= thr else "bad"

    mp = [b["marks_percent"] for b in subjects if b["marks_percent"] is not None]
    m_pct = round(sum(mp) / len(mp), 1) if mp else None
    m_status = None if m_pct is None else "good" if m_pct >= max(75, low_marks) else "warn" if m_pct >= low_marks else "bad"

    done = sum(b["asg_done"] for b in subjects)
    missing = sum(b["asg_missing"] for b in subjects)
    overdue = sum(b["asg_overdue"] for b in subjects)
    upcoming = sum(1 for b in subjects for a in b["assignments"] if a["status"] == "Pending" and not a["overdue"])
    due_total = done + missing + overdue
    a_pct = round(100 * done / due_total, 1) if due_total else None
    a_status = None if a_pct is None else "good" if a_pct >= 85 else "warn" if a_pct >= 60 else "bad"

    # ---- risk reasons
    reasons = []
    low_att = [b["name"] for b in subjects if b["att_low"]]
    if low_att:
        reasons.append(f"Attendance below {thr}% in {', '.join(low_att)}")
    if include_marks:
        low_m = [b["name"] for b in subjects if b["marks_low"]]
        if low_m:
            reasons.append(f"Marks below {low_marks}% in {', '.join(low_m)}")
    if include_asg and missing + overdue >= 2:
        reasons.append(f"{missing + overdue} assignments missing or overdue")
    level = "ok" if not reasons else "watch" if len(reasons) == 1 else "risk"
    label = {"ok": "On track", "watch": "Needs attention", "risk": "At risk"}[level]

    return {
        "student": me, "subjects": subjects, "reasons": reasons, "level": level, "label": label,
        "thresholds": {"attendance": thr, "marks": low_marks},
        "attendance": {"percent": att_pct, "status": att_status, "present": pres, "total": tot},
        "marks": {"available": include_marks, "percent": m_pct, "status": m_status,
                  "grade": grade_for(m_pct) if m_pct is not None else None},
        "assignments": {"available": include_asg, "percent": a_pct, "status": a_status, "done": done,
                        "due_total": due_total, "missing": missing, "overdue": overdue, "upcoming": upcoming},
    }


@app.route("/api/student/standing")
@role_required("student")
def student_standing_api():
    cfg = get_settings()
    st = student_standing(session["user_id"], cfg,
                          include_marks=cfg["student_can_view_marks"],
                          include_asg=cfg["student_can_view_assignments"])
    show_alerts = cfg["student_can_view_alerts"]
    return jsonify(attendance=st["attendance"], marks=st["marks"], assignments=st["assignments"],
                   level=st["level"], label=st["label"], thresholds=st["thresholds"],
                   reasons=st["reasons"] if show_alerts else [], show_alerts=show_alerts)


def overview_rows(subject_ids, cfg):
    """Every approved student in the classes of the given subjects, with their standing
    (each student is judged only on those subjects). Most at-risk first."""
    if not subject_ids:
        return []
    qm = ",".join("?" * len(subject_ids))
    by_class = {}
    for s in query(f"SELECT id, class_name FROM subjects WHERE id IN ({qm})", list(subject_ids)):
        by_class.setdefault(s["class_name"], []).append(s["id"])
    out = []
    for cls, sids in by_class.items():
        for u in query("SELECT id FROM users WHERE role='student' AND status='approved' AND class_name=?", (cls,)):
            st = student_standing(u["id"], cfg, subject_ids=sids)
            out.append({"id": u["id"], "name": st["student"]["name"], "roll_no": st["student"]["roll_no"],
                        "class_name": cls, "level": st["level"], "label": st["label"], "reasons": st["reasons"],
                        "attendance_percent": st["attendance"]["percent"], "marks_percent": st["marks"]["percent"],
                        "assignments_percent": st["assignments"]["percent"]})
    order = {"risk": 0, "watch": 1, "ok": 2}
    out.sort(key=lambda r: (order[r["level"]], -len(r["reasons"]),
                            r["attendance_percent"] if r["attendance_percent"] is not None else 101,
                            r["roll_no"] or ""))
    return out


@app.route("/api/teacher/overview")
@role_required("teacher")
def teacher_overview():
    cfg = get_settings()
    if not cfg["teacher_can_reports"]:
        return bad("Reports are currently disabled by admin", 403)
    sid = request.args.get("subject_id", type=int)
    if sid:
        if not own_subject(sid):
            return bad("Subject not found", 404)
        ids = [sid]
    else:
        ids = [r["id"] for r in query("SELECT id FROM subjects WHERE teacher_id=?", (session["user_id"],))]
    return jsonify(overview_rows(ids, cfg))


@app.route("/api/admin/overview")
@role_required("admin")
def admin_overview():
    cfg = get_settings()
    sid = request.args.get("subject_id", type=int)
    if sid:
        if not query("SELECT 1 FROM subjects WHERE id=?", (sid,), one=True):
            return bad("Subject not found", 404)
        ids = [sid]
    else:
        ids = [r["id"] for r in query("SELECT id FROM subjects")]
    return jsonify(overview_rows(ids, cfg))


def render_report_card(student_id, staff, back_url, subject_ids=None, scope_note=""):
    """staff=True shows everything (admin/teacher); students see only what the admin allows."""
    cfg = get_settings()
    inc_marks = True if staff else cfg["student_can_view_marks"]
    inc_asg = True if staff else cfg["student_can_view_assignments"]
    st = student_standing(student_id, cfg, subject_ids=subject_ids, include_marks=inc_marks, include_asg=inc_asg)
    if not st:
        return "Student not found", 404
    return render_template(
        "report_card.html", st=st, today=date.today().strftime("%d %b %Y"),
        show_remarks=staff or cfg["student_can_view_alerts"], back_url=back_url, scope_note=scope_note)


@app.route("/report-card")
@role_required("student")
def my_report_card():
    return render_report_card(session["user_id"], False, url_for("student_page"))


@app.route("/report-card/<int:sid>")
@role_required("admin")
def admin_report_card(sid):
    return render_report_card(sid, True, url_for("admin_page"))


@app.route("/teacher/report-card/<int:sid>")
@role_required("teacher")
def teacher_report_card(sid):
    """A teacher can open the report card of any student in a class they teach,
    limited to the subjects they teach."""
    if not get_settings()["teacher_can_reports"]:
        return redirect(url_for("teacher_page"))
    stu = query("SELECT class_name FROM users WHERE id=? AND role='student' AND status='approved'", (sid,), one=True)
    subs = query("SELECT id FROM subjects WHERE teacher_id=? AND class_name=?",
                 (session["user_id"], stu["class_name"] if stu else None))
    if not stu or not subs:
        return redirect(url_for("teacher_page"))
    return render_report_card(sid, True, url_for("teacher_page"), subject_ids=[s["id"] for s in subs],
                              scope_note="Shows only the subjects taught by you.")


init_db()

if __name__ == "__main__":
    app.run(debug=True)