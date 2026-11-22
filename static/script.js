/* SmartAttend – shared front-end logic. Each page sets <body data-page="..."> */

const $ = (sel) => document.querySelector(sel);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (isError ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = ""), 2600);
}

async function api(url, method = "GET", body = null) {
  const opts = { method, headers: {} };
  if (body !== null) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (res.status === 401) { window.location = "/login"; throw new Error("Not logged in"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Something went wrong");
  return data;
}

function table(headers, rows, emptyMsg) {
  if (!rows.length) return `<div class="empty">${emptyMsg}</div>`;
  return `<table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

if (window.Chart) {
  Chart.defaults.font.family = '"Figtree", system-ui, sans-serif';
  Chart.defaults.font.size = 13;
  Chart.defaults.color = "#5d6e7b";
  Chart.defaults.borderColor = "#eaeef1";
}

function pctColor(p) { return p >= 85 ? "#0e8a5f" : p >= 75 ? "#e0a21b" : "#c63a2f"; }
function pctBar(p) {
  return `<div style="display:flex;align-items:center;gap:10px">
    <div class="bar" style="flex:1"><div style="width:${p}%;background:${pctColor(p)}"></div></div>
    <b style="min-width:48px">${p}%</b></div>`;
}

function barChart(canvasId, labels, values, existing) {
  if (existing) existing.destroy();
  return new Chart($(canvasId), {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Attendance %",
        data: values,
        backgroundColor: values.map(pctColor),
        borderRadius: 6,
        maxBarThickness: 56,
      }],
    },
    options: {
      maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, max: 100, ticks: { callback: (v) => v + "%" } } },
      plugins: { legend: { display: false } },
    },
  });
}

function lineChart(canvasId, labels, values, existing, threshold) {
  if (existing) existing.destroy();
  const datasets = [{
    label: "Attendance %", data: values, borderColor: "#0f5266", backgroundColor: "rgba(15,82,102,.10)",
    fill: true, tension: 0.3, pointRadius: 5, pointBackgroundColor: values.map(pctColor),
  }];
  if (threshold) datasets.push({
    label: `Minimum (${threshold}%)`, data: labels.map(() => threshold),
    borderColor: "#c63a2f", borderDash: [6, 6], pointRadius: 0, fill: false,
  });
  return new Chart($(canvasId), {
    type: "line", data: { labels, datasets },
    options: {
      maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, max: 100, ticks: { callback: (v) => v + "%" } } },
    },
  });
}

const STATUS_BADGE = { Present: "good", Absent: "bad", Leave: "warn", Medical: "info" };
const statusBadge = (st) => `<span class="badge ${STATUS_BADGE[st] || ""}">${esc(st)}</span>`;

function bindSearch(inputSel, containerSel) {
  const input = $(inputSel);
  const apply = () => {
    const q = input.value.trim().toLowerCase();
    document.querySelectorAll(`${containerSel} tbody tr`).forEach((tr) => {
      tr.style.display = !q || tr.textContent.toLowerCase().includes(q) ? "" : "none";
    });
  };
  input.addEventListener("input", apply);
  return apply;
}

function initReports(base, subjects) {
  $("#rSubject").innerHTML = `<option value="">All subjects</option>` +
    subjects.map((s) => `<option value="${s.id}">${esc(s.class_name)} – ${esc(s.name)}</option>`).join("");
  const applySearch = bindSearch("#rSearch", "#reportTable");

  const qs = (extra = {}) => {
    const p = new URLSearchParams();
    if ($("#rSubject").value) p.set("subject_id", $("#rSubject").value);
    if ($("#rFrom").value) p.set("from", $("#rFrom").value);
    if ($("#rTo").value) p.set("to", $("#rTo").value);
    if ($("#rLow").checked) p.set("low", "1");
    Object.entries(extra).forEach(([k, v]) => p.set(k, v));
    return p.toString();
  };

  async function show() {
    try {
      const r = await api(`${base}/report?${qs()}`);
      const range = r.start || r.end ? ` · ${r.start || "start"} → ${r.end || "today"}` : " · all dates";
      $("#reportTable").innerHTML = (r.rows.length ? `<div class="empty" style="padding-top:0">${r.rows.length} row(s)${range}${$("#rLow").checked ? ` · below ${r.threshold}%` : ""}</div>` : "") +
        table(["Roll no.", "Name", "Subject", "Present", "Absent", "Leave", "Medical", "Attendance"],
          r.rows.map((x) => [esc(x.roll_no), esc(x.name), esc(x.subject), x.present, x.absent, x.on_leave, x.medical,
            x.total ? pctBar(x.percent) : `<span class="badge warn">No data</span>`]),
          $("#rLow").checked ? "No defaulters — everyone is at or above the minimum." : "No records for this selection.");
      applySearch();
    } catch (err) { toast(err.message, true); }
  }

  $("#rShow").addEventListener("click", show);
  $("#rLowBtn").addEventListener("click", () => { $("#rLow").checked = true; show(); });
  $("#rLow").addEventListener("change", show);
  $("#rCsv").addEventListener("click", () => { window.location = `${base}/report?${qs({ format: "csv" })}`; });
}

function initPasswordDialog() {
  const bar = document.querySelector(".topbar .user");
  if (!bar) return;
  const btn = document.createElement("button");
  btn.className = "linklike"; btn.type = "button"; btn.textContent = "Change password";
  bar.insertBefore(btn, bar.querySelector("a"));

  const dlg = document.createElement("dialog");
  dlg.innerHTML = `<form method="dialog" id="pwForm" autocomplete="off">
    <h3>Change password</h3>
    <div class="field"><label>Current password</label><input type="password" name="current_password" required></div>
    <div class="field"><label>New password (min 6)</label><input type="password" name="new_password" minlength="6" required></div>
    <div class="field"><label>Confirm new password</label><input type="password" name="confirm" minlength="6" required></div>
    <div class="err" id="pwErr"></div>
    <div class="actions"><button class="btn secondary" type="button" id="pwCancel">Cancel</button>
      <button class="btn" type="submit">Update</button></div></form>`;
  document.body.appendChild(dlg);

  const form = dlg.querySelector("form");
  btn.addEventListener("click", () => { form.reset(); $("#pwErr").textContent = ""; dlg.showModal(); });
  $("#pwCancel").addEventListener("click", () => dlg.close());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(form).entries());
    if (d.new_password !== d.confirm) { $("#pwErr").textContent = "New passwords do not match"; return; }
    try {
      await api("/api/change-password", "POST", { current_password: d.current_password, new_password: d.new_password });
      dlg.close(); toast("Password updated");
    } catch (err) { $("#pwErr").textContent = err.message; }
  });
}

function attachForm(sel, handler) {
  $(sel).addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = Object.fromEntries(new FormData(form).entries());
    try {
      await handler(data);
      form.reset();
    } catch (err) { toast(err.message, true); }
  });
}

// ---------- LOGIN & SIGNUP ----------
function initLogin() {
  const reg = $("#register");
  if (reg) {
    const pattern = "ppppppapplpppppppppppapppppplppppppppppppapppppppppppplpppapppppppp";
    reg.innerHTML = pattern.split("").map((c, i) => `<i class="${c}" style="--i:${i}"></i>`).join("");
  }

  const allowStudent = document.body.dataset.allowStudent === "1";
  const allowTeacher = document.body.dataset.allowTeacher === "1";

  $("#showLoginBtn").addEventListener("click", () => {
    $("#loginPanel").style.display = "block";
    $("#signupPanel").style.display = "none";
    $("#showLoginBtn").className = "btn";
    $("#showSignupBtn").className = "btn secondary";
  });

  $("#showSignupBtn").addEventListener("click", () => {
    $("#loginPanel").style.display = "none";
    $("#signupPanel").style.display = "block";
    $("#showSignupBtn").className = "btn";
    $("#showLoginBtn").className = "btn secondary";
  });

  const roleSelect = $("#signupRole");
  const roleOptions = [];
  if (allowStudent) roleOptions.push(`<option value="student">Student</option>`);
  if (allowTeacher) roleOptions.push(`<option value="teacher">Teacher</option>`);

  if (!roleOptions.length) {
    roleSelect.innerHTML = `<option value="">Registration disabled</option>`;
    roleSelect.disabled = true;
    $("#signupSubmitBtn").disabled = true;
    $("#signupErr").textContent = "Self-registration is currently closed by admin.";
  } else {
    roleSelect.innerHTML = roleOptions.join("");
  }

  function syncRoleFields() {
    const isStudent = roleSelect.value === "student";
    const isTeacher = roleSelect.value === "teacher";

    $("#studentExtraFields").style.display = isStudent ? "block" : "none";
    $("#signupRoll").required = isStudent;
    $("#signupClass").required = isStudent;
    $("#signupBatch").required = isStudent;

    $("#teacherExtraFields").style.display = isTeacher ? "block" : "none";
    $("#signupEmpId").required = isTeacher;
    $("#signupDesig").required = isTeacher;
  }
  roleSelect.addEventListener("change", syncRoleFields);
  syncRoleFields();

  document.querySelectorAll(".demo-list button").forEach((b) =>
    b.addEventListener("click", () => {
      $("#username").value = b.dataset.u; $("#password").value = b.dataset.p; $("#err").textContent = "";
      $("#loginForm button[type=submit]").focus();
    }));

  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#err").textContent = "";
    try {
      const r = await api("/api/login", "POST", {
        username: $("#username").value,
        password: $("#password").value,
      });
      window.location = r.redirect;
    } catch (err) { $("#err").textContent = err.message; }
  });

  $("#signupForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#signupErr").textContent = "";
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      const r = await api("/api/signup", "POST", data);
      e.target.reset();
      syncRoleFields();
      toast(r.message || "Submitted for admin approval");
      $("#showLoginBtn").click();
    } catch (err) { $("#signupErr").textContent = err.message; }
  });
}

// ---------- ADMIN ----------
function initAdmin() {
  let chart = null;
  let reportsReady = false;

  document.querySelectorAll(".tab-btn").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      $("#tab-" + b.dataset.tab).classList.add("active");
    }));

  async function loadStats() {
    const s = await api("/api/admin/stats");
    $("#st-students").textContent = s.students;
    $("#st-teachers").textContent = s.teachers;
    $("#pendingBadge").textContent = s.pending;
    $("#st-subjects").textContent = s.subjects;
    $("#st-overall").textContent = s.overall + "%";
    chart = barChart("#subjectChart", s.per_subject.map((x) => x.name), s.per_subject.map((x) => x.percent), chart);
  }

  async function loadPending() {
    const rows = await api("/api/admin/pending-users");
    $("#pendingTable").innerHTML = table(
      ["Role", "Applicant", "Contact & Dept", "Academic / Faculty ID", "Verification Note", "Actions"],
      rows.map((r) => {
        const roleTag = r.role === "teacher"
          ? `<span class="badge info">Teacher</span>`
          : `<span class="badge warn">Student</span>`;

        const applicant = `<b>${esc(r.name)}</b><br><small style="color:var(--muted)">@${esc(r.username)} · ${esc(r.created_at || "")}</small>`;
        const contact = `${esc(r.email || "—")}<br><small style="color:var(--muted)">${esc(r.phone || "—")} · Dept: <b>${esc(r.department || "—")}</b></small>`;
        const idInfo = r.role === "student"
          ? `Roll: <b>${esc(r.roll_no || "—")}</b><br><small style="color:var(--muted)">Class: ${esc(r.class_name || "—")} · Batch: ${esc(r.batch_year || "—")}</small>`
          : `Emp ID: <b>${esc(r.employee_id || "—")}</b><br><small style="color:var(--muted)">${esc(r.designation || "—")}</small>`;
        const note = `<span style="white-space:normal;display:inline-block;max-width:240px">${esc(r.verification_note || "No note provided")}</span>`;

        return [
          roleTag,
          applicant,
          contact,
          idInfo,
          note,
          `<button class="btn" data-approve-user="${r.id}" style="padding:6px 12px;margin-right:6px">Approve</button>
           <button class="btn secondary" data-reject-user="${r.id}" style="padding:6px 12px;margin-right:6px">Reject</button>
           <button class="btn danger" data-del-user="${r.id}">Delete</button>`
        ];
      }),
      "No pending student or teacher requests."
    );
  }

  async function loadStudents() {
    const rows = await api("/api/admin/students");
    $("#studentTable").innerHTML = table(
      ["Roll no.", "Name", "Class / Batch", "Dept", "Contact", "Username", ""],
      rows.map((r) => [
        esc(r.roll_no),
        esc(r.name),
        `${esc(r.class_name)}${r.batch_year ? ` (${esc(r.batch_year)})` : ""}`,
        esc(r.department || "—"),
        `${esc(r.email || "—")}${r.phone ? `<br><small style="color:var(--muted)">${esc(r.phone)}</small>` : ""}`,
        esc(r.username),
        `<div class="btn-row"><a class="btn-link" href="/report-card/${r.id}" target="_blank" rel="noopener">Report card</a>
           <button class="btn danger" data-del-user="${r.id}">Delete</button></div>`
      ]),
      "No approved students yet.");
    applyStudentSearch();
  }

  async function loadTeachers() {
    const rows = await api("/api/admin/teachers");
    $("#teacherTable").innerHTML = table(
      ["Emp ID", "Name", "Designation / Dept", "Contact", "Username", ""],
      rows.map((r) => [
        esc(r.employee_id || "—"),
        esc(r.name),
        `${esc(r.designation || "—")}${r.department ? ` · ${esc(r.department)}` : ""}`,
        `${esc(r.email || "—")}${r.phone ? `<br><small style="color:var(--muted)">${esc(r.phone)}</small>` : ""}`,
        esc(r.username),
        `<button class="btn danger" data-del-user="${r.id}">Delete</button>`
      ]),
      "No approved teachers yet.");
    applyTeacherSearch();
    $("#teacherSelect").innerHTML = rows.length
      ? rows.map((r) => `<option value="${r.id}">${esc(r.name)} (${esc(r.employee_id || r.username)})</option>`).join("")
      : `<option value="">Add or approve a teacher first</option>`;
  }

  async function loadSubjects() {
    const rows = await api("/api/admin/subjects");
    $("#subjectTable").innerHTML = table(
      ["Code", "Subject", "Class", "Teacher", ""],
      rows.map((r) => [esc(r.code), esc(r.name), esc(r.class_name), esc(r.teacher || "—"),
        `<button class="btn danger" data-del-subject="${r.id}">Delete</button>`]),
      "No subjects yet.");
    if (!reportsReady) { reportsReady = true; initReports("/api/admin", rows); initRisk("/api/admin", "/report-card"); }
    else $("#rSubject").innerHTML = `<option value="">All subjects</option>` +
      rows.map((s) => `<option value="${s.id}">${esc(s.class_name)} – ${esc(s.name)}</option>`).join("");
  }

  async function loadSettings() {
    const cfg = await api("/api/admin/settings");
    const form = $("#settingsForm");
    form.low_attendance.value = cfg.low_attendance;
    form.low_marks.value = cfg.low_marks;
    [
      "allow_student_signup", "allow_teacher_signup",
      "teacher_can_mark", "teacher_can_edit", "teacher_can_reports", "teacher_allow_future_dates",
      "student_can_view_history", "student_can_view_trend", "student_can_view_alerts",
      "teacher_can_manage_academics", "student_can_view_marks", "student_can_view_class_avg", "student_can_view_assignments",
    ].forEach((k) => {
      if (form[k]) form[k].checked = Boolean(cfg[k]);
    });
  }

  const applyStudentSearch = bindSearch("#studentSearch", "#studentTable");
  const applyTeacherSearch = bindSearch("#teacherSearch", "#teacherTable");
  const refreshAll = () => Promise.all([loadStats(), loadPending(), loadStudents(), loadTeachers(), loadSubjects(), loadSettings()]);

  attachForm("#studentForm", async (d) => { await api("/api/admin/students", "POST", d); toast("Student added & approved"); refreshAll(); });
  attachForm("#teacherForm", async (d) => { await api("/api/admin/teachers", "POST", d); toast("Teacher added & approved"); refreshAll(); });
  attachForm("#subjectForm", async (d) => { await api("/api/admin/subjects", "POST", d); toast("Subject added"); refreshAll(); });

  $("#settingsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const payload = {
      low_attendance: Number(form.low_attendance.value),
      low_marks: Number(form.low_marks.value),
      allow_student_signup: form.allow_student_signup.checked,
      allow_teacher_signup: form.allow_teacher_signup.checked,
      teacher_can_mark: form.teacher_can_mark.checked,
      teacher_can_edit: form.teacher_can_edit.checked,
      teacher_can_reports: form.teacher_can_reports.checked,
      teacher_allow_future_dates: form.teacher_allow_future_dates.checked,
      student_can_view_history: form.student_can_view_history.checked,
      student_can_view_trend: form.student_can_view_trend.checked,
      student_can_view_alerts: form.student_can_view_alerts.checked,
      teacher_can_manage_academics: form.teacher_can_manage_academics.checked,
      student_can_view_marks: form.student_can_view_marks.checked,
      student_can_view_class_avg: form.student_can_view_class_avg.checked,
      student_can_view_assignments: form.student_can_view_assignments.checked,
    };
    try {
      await api("/api/admin/settings", "POST", payload);
      toast("Access settings updated");
    } catch (err) { toast(err.message, true); }
  });

  document.addEventListener("click", async (e) => {
    const appId = e.target.dataset.approveUser;
    const rejId = e.target.dataset.rejectUser;
    const u = e.target.dataset.delUser;
    const s = e.target.dataset.delSubject;
    try {
      if (appId) {
        await api(`/api/admin/users/${appId}/status`, "POST", { status: "approved" });
        toast("Account approved");
        refreshAll();
      } else if (rejId) {
        await api(`/api/admin/users/${rejId}/status`, "POST", { status: "rejected" });
        toast("Account rejected");
        refreshAll();
      } else if (u && confirm("Delete this user and all their attendance records?")) {
        await api("/api/admin/users/" + u, "DELETE");
        toast("Deleted");
        refreshAll();
      } else if (s && confirm("Delete this subject and its attendance records?")) {
        await api("/api/admin/subjects/" + s, "DELETE");
        toast("Deleted");
        refreshAll();
      }
    } catch (err) { toast(err.message, true); }
  });

  refreshAll();
}

// ---------- TEACHER ----------
function initTeacher() {
  const today = new Date();
  const iso = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  $("#dateInput").value = iso;

  let students = [];
  let perms = {};
  const STATUSES = ["Present", "Absent", "Leave", "Medical"];
  const applyMarkSearch = bindSearch("#markSearch", "#markTable");

  document.querySelectorAll(".tab-btn").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      $("#tab-" + b.dataset.tab).classList.add("active");
    }));

  api("/api/teacher/subjects").then((data) => {
    const subs = data.subjects || [];
    perms = data.permissions || {};
    initTeacherAcademics(subs, perms);

    if (!perms.teacher_allow_future_dates) {
      $("#dateInput").max = iso;
    }

    if (!perms.teacher_can_mark && !perms.teacher_can_edit) {
      $("#markSection").style.display = "none";
      $("#notice").innerHTML = `<div class="alert low">Attendance marking and editing are currently disabled by admin.</div>`;
    }

    if (perms.teacher_can_reports) {
      initReports("/api/teacher", subs);
      initRisk("/api/teacher", "/teacher/report-card");
    } else {
      document.querySelector('[data-tab="reports"]').remove();
      $("#tab-reports").remove();
      $("#teacherReportsSection")?.remove();
    }

    if (!subs.length) {
      $("#subjectSelect").innerHTML = `<option value="">No subjects assigned</option>`;
      $("#notice").innerHTML = `<div class="alert low">No subjects are assigned to you yet. Ask the admin.</div>`;
      return;
    }
    $("#subjectSelect").innerHTML = subs.map((s) =>
      `<option value="${s.id}">${esc(s.class_name)} – ${esc(s.name)} (${esc(s.code)})</option>`).join("");
  });

  async function load() {
    const sid = $("#subjectSelect").value, day = $("#dateInput").value;
    if (!sid) return toast("Select a subject", true);
    if (!day) return toast("Select a date", true);
    try {
      const r = await api(`/api/teacher/students?subject_id=${sid}&date=${day}`);
      students = r.students;
      $("#saveBtn").disabled = !r.can_save;
      $("#notice").innerHTML = r.marked
        ? `<div class="alert ${r.can_save ? "ok" : "low"}">${
            r.can_save
              ? "Attendance already marked for this date. You can edit it below."
              : "Attendance already marked. Editing is disabled by admin."
          }</div>`
        : "";
      $("#markTitle").textContent = $("#subjectSelect").selectedOptions[0].textContent + " · " + day;
      $("#markCard").style.display = "block";
      render();
    } catch (err) { toast(err.message, true); }
  }

  function render() {
    if (!students.length) {
      $("#markTable").innerHTML = `<div class="empty">No approved students in this class.</div>`;
      return;
    }
    $("#markTable").innerHTML = table(
      ["Roll no.", "Name", "Attended", "Absent", "Excused", "Total classes", "Mark today"],
      students.map((s) => {
        const current = s.status || "Present";
        return [esc(s.roll_no), esc(s.name),
          `<b>${s.present}</b>`, s.absent, s.excused, s.total,
          `<div class="toggle">${STATUSES.map((st) =>
            `<label class="${st.toLowerCase()}"><input type="radio" name="st${s.id}" value="${st}" ${st === current ? "checked" : ""}><span class="p">${st}</span></label>`
          ).join("")}</div>`];
      }), "");
    applyMarkSearch();
  }

  function setAll(value) {
    document.querySelectorAll(`#markTable input[value="${value}"]`).forEach((i) => (i.checked = true));
  }

  async function save() {
    const records = students.map((s) => ({
      student_id: s.id,
      status: document.querySelector(`input[name="st${s.id}"]:checked`).value,
    }));
    try {
      const r = await api("/api/teacher/attendance", "POST", {
        subject_id: Number($("#subjectSelect").value),
        date: $("#dateInput").value,
        records,
      });
      toast(`Attendance saved for ${r.saved} students`);
      await load();
      $("#notice").innerHTML = `<div class="alert ok">Attendance saved.</div>`;
    } catch (err) { toast(err.message, true); }
  }

  $("#loadBtn").addEventListener("click", load);
  $("#allPresent").addEventListener("click", () => setAll("Present"));
  $("#allAbsent").addEventListener("click", () => setAll("Absent"));
  $("#saveBtn").addEventListener("click", save);
}

// ---------- TEACHER: MARKS & ASSIGNMENTS ----------
const fmtNum = (n) => (Number.isInteger(+n) ? String(+n) : (+n).toFixed(1));
const isoLocal = (offsetDays = 0) => {
  const t = new Date();
  return new Date(t.getTime() - t.getTimezoneOffset() * 60000 + offsetDays * 864e5).toISOString().slice(0, 10);
};

function initTeacherAcademics(subs, perms) {
  if (!perms.teacher_can_manage_academics) {
    document.querySelectorAll('[data-tab="marks"], [data-tab="assignments"], #tab-marks, #tab-assignments')
      .forEach((el) => el.remove());
    return;
  }
  const opts = subs.length
    ? subs.map((s) => `<option value="${s.id}">${esc(s.class_name)} – ${esc(s.name)} (${esc(s.code)})</option>`).join("")
    : `<option value="">No subjects assigned</option>`;
  $("#amSubject").innerHTML = opts;
  $("#asSubject").innerHTML = opts;
  $("#amDate").defaultValue = $("#amDate").value = isoLocal(0);
  $("#asDue").defaultValue = $("#asDue").value = isoLocal(7);

  /* ---------- marks ---------- */
  let assessment = null, entryStudents = [];

  async function loadAssessments() {
    const rows = await api("/api/teacher/assessments");
    $("#assessTable").innerHTML = table(
      ["Class & subject", "Assessment", "Type", "Date", "Max", "Weight", "Entered", "Class avg", "Status", ""],
      rows.map((r) => [
        `${esc(r.class_name)} – ${esc(r.subject)}`, `<b>${esc(r.title)}</b>`, esc(r.type), esc(r.date),
        fmtNum(r.max_marks), `${fmtNum(r.weightage)}%`, `${r.entered}/${r.students}`,
        r.avg_percent == null ? "—" : pctBar(r.avg_percent),
        r.published ? `<span class="badge good">Published</span>` : `<span class="badge warn">Hidden</span>`,
        `<div class="btn-row">
           <button class="btn secondary small" data-enter="${r.id}">Enter marks</button>
           <button class="btn secondary small" data-publish="${r.id}" data-to="${r.published ? 0 : 1}">${r.published ? "Unpublish" : "Publish"}</button>
           <button class="btn danger" data-del-assess="${r.id}">Delete</button></div>`,
      ]),
      "No assessments yet. Create one above.");
  }

  const pctText = (score, absent, max) =>
    absent ? "Absent" : score === "" || score == null ? "—" : `${Math.round((100 * score) / max)}%`;

  async function openMarks(id) {
    const r = await api(`/api/teacher/assessments/${id}/marks`);
    assessment = r.assessment; entryStudents = r.students;
    const max = assessment.max_marks;
    $("#entryTitle").textContent = `${assessment.class_name} – ${assessment.subject} · ${assessment.title} (out of ${fmtNum(max)})`;
    $("#entryTable").innerHTML = table(
      ["Roll no.", "Name", "Marks", "Absent", "%"],
      entryStudents.map((s) => [
        esc(s.roll_no), esc(s.name),
        `<input class="score-input" type="number" min="0" max="${max}" step="0.5" data-sid="${s.id}" value="${s.score ?? ""}" ${s.absent ? "disabled" : ""}>`,
        `<label class="check tight"><input type="checkbox" data-abs="${s.id}" ${s.absent ? "checked" : ""}> Absent</label>`,
        `<b data-pct="${s.id}">${pctText(s.score, s.absent, max)}</b>`,
      ]),
      "No approved students in this class.");
    $("#entryCard").style.display = "block";
    $("#entryCard").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("#entryTable").addEventListener("input", (e) => {
    const sid = e.target.dataset.sid;
    if (sid) $(`[data-pct="${sid}"]`).textContent = pctText(e.target.value, false, assessment.max_marks);
  });
  $("#entryTable").addEventListener("change", (e) => {
    const sid = e.target.dataset.abs;
    if (!sid) return;
    const inp = $(`#entryTable input[data-sid="${sid}"]`);
    inp.disabled = e.target.checked;
    if (e.target.checked) inp.value = "";
    $(`[data-pct="${sid}"]`).textContent = pctText(inp.value, e.target.checked, assessment.max_marks);
  });
  $("#entryClose").addEventListener("click", () => ($("#entryCard").style.display = "none"));
  $("#entrySave").addEventListener("click", async () => {
    const records = entryStudents.map((s) => ({
      student_id: s.id,
      score: $(`#entryTable input[data-sid="${s.id}"]`).value,
      absent: $(`#entryTable input[data-abs="${s.id}"]`).checked,
    }));
    try {
      const r = await api(`/api/teacher/assessments/${assessment.id}/marks`, "POST", { records });
      toast(`Marks saved for ${r.saved} students`);
      loadAssessments();
    } catch (err) { toast(err.message, true); }
  });

  attachForm("#assessForm", async (d) => {
    await api("/api/teacher/assessments", "POST", d);
    toast("Assessment created — enter marks below");
    await loadAssessments();
  });

  $("#tab-marks").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    try {
      if (b.dataset.enter) await openMarks(b.dataset.enter);
      else if (b.dataset.publish) {
        await api(`/api/teacher/assessments/${b.dataset.publish}/publish`, "POST", { published: b.dataset.to === "1" });
        toast(b.dataset.to === "1" ? "Published — students can now see it" : "Hidden from students");
        loadAssessments();
      } else if (b.dataset.delAssess && confirm("Delete this assessment and all its marks?")) {
        await api(`/api/teacher/assessments/${b.dataset.delAssess}`, "DELETE");
        if (assessment && String(assessment.id) === b.dataset.delAssess) $("#entryCard").style.display = "none";
        toast("Deleted"); loadAssessments();
      }
    } catch (err) { toast(err.message, true); }
  });

  /* ---------- assignments ---------- */
  let assignment = null, trackStudents = [];
  const ST = ["Pending", "Submitted", "Late", "Missing"];

  async function loadAssignments() {
    const rows = await api("/api/teacher/assignments");
    $("#assignTable").innerHTML = table(
      ["Class & subject", "Assignment", "Due", "Max", "Submitted", ""],
      rows.map((r) => [
        `${esc(r.class_name)} – ${esc(r.subject)}`,
        `<b>${esc(r.title)}</b>${r.description ? `<br><small class="muted">${esc(r.description)}</small>` : ""}`,
        esc(r.due_date), fmtNum(r.max_marks),
        `<b>${r.submitted}</b>/${r.students}`,
        `<div class="btn-row"><button class="btn secondary small" data-track="${r.id}">Track submissions</button>
           <button class="btn danger" data-del-asg="${r.id}">Delete</button></div>`,
      ]),
      "No assignments yet. Post one above.");
  }

  async function openTrack(id) {
    const r = await api(`/api/teacher/assignments/${id}/submissions`);
    assignment = r.assignment; trackStudents = r.students;
    $("#trackTitle").textContent = `${assignment.class_name} – ${assignment.subject} · ${assignment.title} (due ${assignment.due_date}, out of ${fmtNum(assignment.max_marks)})`;
    $("#trackTable").innerHTML = table(
      ["Roll no.", "Name", "Status", "Marks", "Feedback"],
      trackStudents.map((s) => [
        esc(s.roll_no), esc(s.name),
        `<select data-st="${s.id}">${ST.map((x) => `<option ${x === s.status ? "selected" : ""}>${x}</option>`).join("")}</select>`,
        `<input class="score-input" type="number" min="0" max="${assignment.max_marks}" step="0.5" data-mk="${s.id}" value="${s.marks ?? ""}">`,
        `<input data-fb="${s.id}" maxlength="500" placeholder="Optional feedback" value="${esc(s.feedback)}" style="min-width:180px">`,
      ]),
      "No approved students in this class.");
    $("#trackCard").style.display = "block";
    $("#trackCard").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("#trackAll").addEventListener("click", () =>
    document.querySelectorAll("#trackTable select[data-st]").forEach((s) => { if (s.value === "Pending") s.value = "Submitted"; }));
  $("#trackClose").addEventListener("click", () => ($("#trackCard").style.display = "none"));
  $("#trackSave").addEventListener("click", async () => {
    const records = trackStudents.map((s) => ({
      student_id: s.id,
      status: $(`#trackTable [data-st="${s.id}"]`).value,
      marks: $(`#trackTable [data-mk="${s.id}"]`).value,
      feedback: $(`#trackTable [data-fb="${s.id}"]`).value,
    }));
    try {
      const r = await api(`/api/teacher/assignments/${assignment.id}/submissions`, "POST", { records });
      toast(`Saved ${r.saved} submissions`);
      loadAssignments();
    } catch (err) { toast(err.message, true); }
  });

  attachForm("#assignForm", async (d) => {
    await api("/api/teacher/assignments", "POST", d);
    toast("Assignment posted");
    await loadAssignments();
  });

  $("#tab-assignments").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    try {
      if (b.dataset.track) await openTrack(b.dataset.track);
      else if (b.dataset.delAsg && confirm("Delete this assignment and all its submissions?")) {
        await api(`/api/teacher/assignments/${b.dataset.delAsg}`, "DELETE");
        if (assignment && String(assignment.id) === b.dataset.delAsg) $("#trackCard").style.display = "none";
        toast("Deleted"); loadAssignments();
      }
    } catch (err) { toast(err.message, true); }
  });

  loadAssessments().catch((err) => toast(err.message, true));
  loadAssignments().catch((err) => toast(err.message, true));
}

// ---------- STUDENT ----------
async function initStudent() {
  let chart = null;
  const s = await api("/api/student/summary");
  const perms = s.permissions || {};
  initStudentAcademics(perms);
  initStandingCard();

  $("#st-overall").textContent = s.overall + "%";
  $("#ring").style.setProperty("--p", s.overall);
  $("#ring").style.setProperty("--c", s.overall >= 85 ? "#2cc08f" : s.overall >= s.threshold ? "#f2b01e" : "#ef6a5e");
  $("#st-class").textContent = s.student.class_name;
  $("#st-roll").textContent = s.student.roll_no;

  if (perms.student_can_view_alerts !== false) {
    const low = s.subjects.filter((x) => x.low);
    $("#alerts").innerHTML = low.length
      ? low.map((x) => `<div class="alert low"><b>Low attendance:</b> ${esc(x.name)} is at ${x.percent}% (minimum ${s.threshold}%).
          Attend the next <b>${x.need}</b> class${x.need > 1 ? "es" : ""} in a row to get back on track.</div>`).join("")
      : `<div class="alert ok">Great job! Your attendance is above ${s.threshold}% in every subject.</div>`;
  } else {
    $("#alerts").innerHTML = "";
  }

  chart = barChart("#subjectChart", s.subjects.map((x) => x.name), s.subjects.map((x) => x.percent), chart);

  $("#summaryTable").innerHTML = table(
    ["Subject", "Code", "Present", "Excused", "Total", "Attendance", "Status"],
    s.subjects.map((x) => [esc(x.name), esc(x.code), x.present, x.excused, x.total, pctBar(x.percent),
      x.total === 0 ? `<span class="badge warn">No data</span>`
        : x.low ? `<span class="badge bad">Low</span>` : `<span class="badge good">OK</span>`]),
    "No subjects found for your class.");

  if (perms.student_can_view_history !== false) {
    $("#historyFilter").innerHTML = `<option value="">All subjects</option>` +
      s.subjects.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("");

    async function loadHistory() {
      const id = $("#historyFilter").value;
      const rows = await api("/api/student/history" + (id ? `?subject_id=${id}` : ""));
      $("#historyTable").innerHTML = table(
        ["Date", "Subject", "Status"],
        rows.map((r) => [esc(r.date), esc(r.subject), statusBadge(r.status)]),
        "No attendance records yet.");
    }
    $("#historyFilter").addEventListener("change", loadHistory);
    loadHistory();
  } else {
    $("#historySection").style.display = "none";
  }

  if (perms.student_can_view_trend !== false) {
    let trend = null;
    async function loadTrend(by) {
      const rows = await api("/api/student/trend?by=" + by);
      trend = lineChart("#trendChart", rows.map((r) => r.period), rows.map((r) => r.percent), trend, s.threshold);
      $("#trendNote").textContent = rows.length ? "" : "No attendance records yet.";
    }
    const months = await api("/api/student/trend?by=month");
    const by = months.length >= 3 ? "month" : "week";
    $("#trendBy").value = by;
    loadTrend(by);
    $("#trendBy").addEventListener("change", (e) => loadTrend(e.target.value));
  } else {
    $("#trendSection").style.display = "none";
  }
}

// ---------- STUDENT: MARKS & ASSIGNMENTS ----------
function dueText(d) {
  const days = Math.round((new Date(d + "T00:00:00") - new Date(new Date().toDateString())) / 864e5);
  if (days === 0) return "due today";
  return days > 0 ? `due in ${days} day${days > 1 ? "s" : ""}` : `${-days} day${days < -1 ? "s" : ""} ago`;
}
const gradeChip = (g) => (g ? `<span class="grade ${g[0].toLowerCase()}">${esc(g)}</span>` : "");
const ASG_BADGE = { Submitted: "good", Late: "warn", Missing: "bad", Pending: "info", Overdue: "bad" };

async function initStudentAcademics(perms) {
  const showMarks = perms.student_can_view_marks !== false;
  const showAsg = perms.student_can_view_assignments !== false;
  const hide = (sel) => { const el = $(sel); if (el) el.style.display = "none"; };
  if (!showMarks) { hide("#marksSection"); }
  if (!showAsg) { hide("#assignSection"); }

  if (showMarks) {
    try {
      const m = await api("/api/student/marks");
      $("#marksSubjects").innerHTML = m.subjects.length
        ? m.subjects.map((x) => `<div class="tile">
            <div class="t-name">${esc(x.name)}</div><div class="t-code">${esc(x.code)} · ${x.count} result${x.count === 1 ? "" : "s"}</div>
            ${x.percent == null ? `<div class="empty" style="padding:10px 0 0;text-align:left">No results yet</div>`
              : `<div class="t-row"><span class="t-pct">${x.percent}%</span>${gradeChip(x.grade)}</div>${pctBar(x.percent)}`}
          </div>`).join("")
        : "";
      $("#marksTable").innerHTML = table(
        ["Subject", "Assessment", "Type", "Date", "Score", "Percent", "Class avg", "Grade"],
        m.results.map((r) => [
          esc(r.subject), `<b>${esc(r.title)}</b>`, esc(r.type), esc(r.date),
          r.is_absent ? `<span class="badge bad">Absent</span>`
            : r.has_result ? `<b>${fmtNum(r.score)}</b> / ${fmtNum(r.max_marks)}` : `<span class="badge warn">Not graded</span>`,
          r.has_result ? pctBar(r.percent) : "—",
          r.class_avg_percent == null ? "—" : r.class_avg_percent + "%",
          gradeChip(r.grade) || "—",
        ]),
        "No results have been published yet.");
    } catch (err) { toast(err.message, true); }
  }

  if (showAsg) {
    try {
      const a = await api("/api/student/assignments");
      $("#assignTable").innerHTML = table(
        ["Subject", "Assignment", "Due", "Status", "Marks", "Feedback"],
        a.assignments.map((r) => {
          const st = r.overdue ? "Overdue" : r.status;
          return [
            esc(r.subject),
            `<b>${esc(r.title)}</b>${r.description ? `<br><small class="muted">${esc(r.description)}</small>` : ""}`,
            `${esc(r.due_date)}<br><small class="muted">${dueText(r.due_date)}</small>`,
            `<span class="badge ${ASG_BADGE[st] || ""}">${st}</span>`,
            r.marks == null ? "—" : `<b>${fmtNum(r.marks)}</b> / ${fmtNum(r.max_marks)}`,
            r.feedback ? esc(r.feedback) : "—",
          ];
        }),
        "No assignments have been posted yet.");
    } catch (err) { toast(err.message, true); }
  }
}

// ---------- STUDENT: STANDING ----------
async function initStandingCard() {
  try {
    const r = await api("/api/student/standing");
    const colors = { good: "#0e8a5f", warn: "#e0a21b", bad: "#c63a2f" };
    const ring = (label, pct, status, sub) => `<div class="m-ring">
      <div class="mini-ring" style="--p:${pct ?? 0};--c:${colors[status] || "#cfd8de"}"><span>${pct == null ? "–" : Math.round(pct) + "%"}</span></div>
      <div class="m-label">${label}</div><div class="m-sub">${sub}</div></div>`;
    const parts = [ring("Attendance", r.attendance.percent, r.attendance.status,
      r.attendance.total ? `${r.attendance.present} of ${r.attendance.total} classes` : "No classes yet")];
    if (r.marks.available) parts.push(ring("Marks", r.marks.percent, r.marks.status,
      r.marks.grade ? `Average · Grade ${r.marks.grade}` : "No results yet"));
    if (r.assignments.available) {
      const a = r.assignments;
      parts.push(ring("Assignments", a.percent, a.status,
        a.due_total ? `${a.done} of ${a.due_total} done${a.overdue ? ` · ${a.overdue} overdue` : ""}` : (a.upcoming ? `${a.upcoming} upcoming` : "None due yet")));
    }
    $("#standingRings").innerHTML = parts.join("");
    const lv = { ok: "good", watch: "warn", risk: "bad" }[r.level];
    $("#standingBadge").innerHTML = `<span class="badge ${lv}">${esc(r.label)}</span>`;
    $("#standingNotes").innerHTML = !r.show_alerts ? ""
      : r.reasons.length
        ? `<ul class="reasons">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`
        : `<p class="muted" style="margin:14px 0 0">No concerns right now — keep it up.</p>`;
  } catch (err) { $("#standingCard").style.display = "none"; }
}

// ---------- STUDENT OVERVIEW (teacher & admin) ----------
function initRisk(base, reportPrefix) {
  const LV = { risk: "bad", watch: "warn", ok: "good" };
  const pc = (p) => (p == null ? "—" : `<b style="color:${pctColor(p)}">${p}%</b>`);
  let rows = [];

  function render() {
    const f = $("#riskFilter").value, q = $("#riskSearch").value.trim().toLowerCase();
    const shown = rows.filter((r) =>
      (!f || (f === "flagged" ? r.level !== "ok" : r.level === f)) &&
      (!q || `${r.name} ${r.roll_no} ${r.class_name}`.toLowerCase().includes(q)));
    $("#riskTable").innerHTML = table(
      ["Roll no.", "Student", "Class", "Status", "Attendance", "Marks", "Assignments", "Why", ""],
      shown.map((r) => [
        esc(r.roll_no), `<b>${esc(r.name)}</b>`, esc(r.class_name),
        `<span class="badge ${LV[r.level]}">${esc(r.label)}</span>`,
        pc(r.attendance_percent), pc(r.marks_percent), pc(r.assignments_percent),
        r.reasons.length ? `<ul class="reasons tight">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<span class="muted">—</span>`,
        `<a class="btn-link" href="${reportPrefix}/${r.id}" target="_blank" rel="noopener">Report card</a>`,
      ]),
      rows.length ? "No students match this filter." : "No approved students found.");
    const n = (l) => rows.filter((r) => r.level === l).length;
    $("#riskCount").textContent = rows.length
      ? `${rows.length} students · ${n("risk")} at risk · ${n("watch")} need attention · ${n("ok")} on track` : "";
  }

  async function load() {
    try { rows = await api(`${base}/overview`); render(); }
    catch (err) { toast(err.message, true); }
  }
  $("#riskFilter").addEventListener("change", render);
  $("#riskSearch").addEventListener("input", render);
  $("#riskBtn").addEventListener("click", load);
  load();
}

// ---------- boot ----------
document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  initPasswordDialog();
  ({ login: initLogin, admin: initAdmin, teacher: initTeacher, student: initStudent }[page] || (() => {}))();
});