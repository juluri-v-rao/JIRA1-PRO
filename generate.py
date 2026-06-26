#!/usr/bin/env python3
"""Fetches TMS02 JIRA data and generates a static index.html dashboard."""

import os, json, base64, urllib.request, datetime

DOMAIN  = os.environ["JIRA_DOMAIN"]
EMAIL   = os.environ["JIRA_EMAIL"]
TOKEN   = os.environ["JIRA_TOKEN"]
PROJECT = os.environ.get("JIRA_PROJECT", "TMS02")

AUTH    = base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
BASE    = f"https://{DOMAIN}/rest/api/3"
HEADERS = {"Authorization": f"Basic {AUTH}", "Accept": "application/json",
           "Content-Type": "application/json"}

COMMON_PHASES = ["Screen Design", "Requirements", "API Design"]
STATUS_PCT = {
    "not started": 0,   "未着手": 0,
    "in progress": 30,  "作成中": 30,
    "under review": 80, "内部レビュー中": 80,
    "approval complete": 100, "承認完了": 100,
    "done": 100, "closed": 100, "resolved": 100,
}

# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _request(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(f"{BASE}/{path}", data=data,
                                  headers=HEADERS, method=method)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def get(path):         return _request("GET",  path)
def post(path, body):  return _request("POST", path, body)

# ── JIRA helpers ──────────────────────────────────────────────────────────────

def search(jql, fields):
    """Fetch all issues matching JQL using cursor-based pagination."""
    issues, token = [], None
    while True:
        body = {"jql": jql, "fields": fields, "maxResults": 100}
        if token:
            body["nextPageToken"] = token
        resp   = post("search/jql", body)
        batch  = resp.get("issues", [])
        issues += batch
        token  = resp.get("nextPageToken")
        if not token or not batch:
            break
    return issues

def issue(key, fields):
    f = ",".join(fields)
    return get(f"issue/{key}?fields={f}")

def hrs(seconds):
    return (seconds or 0) / 3600

def status_pct(name):
    if not name:
        return 0
    nl = name.lower().strip()
    for key, val in STATUS_PCT.items():
        if key in nl:
            return val
    return 0

# ── Data building ─────────────────────────────────────────────────────────────

def build_data():
    epics = search(
        f"project={PROJECT} AND issuetype=Epic ORDER BY created ASC",
        ["summary", "status", "timeoriginalestimate", "timespent"],
    )

    result = []
    for epic in epics:
        ek = epic["key"]

        # Try Epic Link first (classic projects), fall back to parent (next-gen)
        task_fields = ["summary", "status", "timeoriginalestimate", "timespent", "subtasks"]
        tasks = search(
            f'project={PROJECT} AND "Epic Link"={ek} AND issuetype not in (Epic, Sub-task)',
            task_fields,
        )
        if not tasks:
            tasks = search(
                f"project={PROJECT} AND parent={ek} AND issuetype not in (Epic, Sub-task)",
                task_fields,
            )

        task_data = []
        for task in tasks:
            subtask_refs = task["fields"].get("subtasks") or []
            subs = []

            for ref in subtask_refs:
                try:
                    d = issue(ref["key"], ["summary", "status",
                                           "timeoriginalestimate", "timespent"])
                    f = d["fields"]
                    subs.append({
                        "key":        ref["key"],
                        "name":       f["summary"],
                        "status":     f["status"]["name"],
                        "plannedHrs": hrs(f.get("timeoriginalestimate")),
                        "spentHrs":   hrs(f.get("timespent")),
                        "pct":        status_pct(f["status"]["name"]),
                    })
                except Exception:
                    pass

            # If task has no subtasks, treat the task itself as the leaf
            if not subs:
                tf = task["fields"]
                subs.append({
                    "key":        task["key"],
                    "name":       tf["summary"],
                    "status":     tf["status"]["name"],
                    "plannedHrs": hrs(tf.get("timeoriginalestimate")),
                    "spentHrs":   hrs(tf.get("timespent")),
                    "pct":        status_pct(tf["status"]["name"]),
                })

            tp = sum(s["plannedHrs"] for s in subs)
            tw = sum(s["plannedHrs"] * s["pct"] for s in subs)
            tf = task["fields"]
            task_data.append({
                "key":        task["key"],
                "name":       tf["summary"],
                "plannedHrs": tp or hrs(tf.get("timeoriginalestimate")),
                "spentHrs":   sum(s["spentHrs"] for s in subs),
                "pct":        (tw / tp) if tp else status_pct(tf["status"]["name"]),
                "subtasks":   subs,
            })

        ep = sum(t["plannedHrs"] for t in task_data)
        ew = sum(t["plannedHrs"] * t["pct"] for t in task_data)
        ef = epic["fields"]
        epic_pct = (ew / ep) if ep else status_pct(ef["status"]["name"])

        # Group subtasks into common phases vs others
        all_subs = [s for t in task_data for s in t["subtasks"]]
        phases, others = {}, []
        for sub in all_subs:
            matched = False
            for ph in COMMON_PHASES:
                if ph.lower() in sub["name"].lower():
                    phases.setdefault(ph, []).append(sub)
                    matched = True
                    break
            if not matched:
                others.append(sub)

        phase_stats = {}
        for ph, ss in phases.items():
            pl = sum(s["plannedHrs"] for s in ss)
            ws = sum(s["plannedHrs"] * s["pct"] for s in ss)
            phase_stats[ph] = {
                "pct":        (ws / pl) if pl else 0,
                "plannedHrs": pl,
                "spentHrs":   sum(s["spentHrs"] for s in ss),
            }

        result.append({
            "key":           ek,
            "title":         ef["summary"],
            "epicPct":       epic_pct,
            "epicPlanned":   ep,
            "epicSpent":     sum(t["spentHrs"] for t in task_data),
            "phaseStats":    phase_stats,
            "otherSubtasks": others,
        })

    return result

# ── HTML rendering ────────────────────────────────────────────────────────────

def render_html(data, generated_at):
    total_planned  = sum(e["epicPlanned"] for e in data)
    total_weighted = sum(e["epicPlanned"] * e["epicPct"] for e in data)
    overall        = round((total_weighted / total_planned) if total_planned else 0)

    def bar_color(p):
        if p >= 100: return "#00c48c"
        if p >= 80:  return "#4f8ef7"
        if p >= 30:  return "#ffc542"
        return "#8892a4"

    def fmt(h):
        d, r = int(h // 8), round(h % 8, 1)
        if d and r: return f"{d}d {r}h"
        if d:       return f"{d}d"
        return f"{r}h"

    def pill(p):
        if p >= 100: return "pill-ac", "Approval Complete"
        if p >= 80:  return "pill-ur", "Under Review"
        if p >= 1:   return "pill-ip", "In Progress"
        return "pill-ns", "Not Started"

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;") \
                     .replace(">", "&gt;").replace('"', "&quot;")

    cards = []
    for e in data:
        pct = round(e["epicPct"])
        col = bar_color(pct)

        phase_rows = ""
        for ph, s in e["phaseStats"].items():
            pp = round(s["pct"])
            phase_rows += (
                f'<div class="phase-row">'
                f'<span class="phase-name">{esc(ph)}</span>'
                f'<div class="phase-bar-track">'
                f'<div class="phase-bar-fill" style="width:{pp}%;background:{bar_color(pp)}"></div>'
                f'</div>'
                f'<span class="phase-pct" style="color:{bar_color(pp)}">{pp}%</span>'
                f'<span class="phase-eff">{fmt(s["plannedHrs"])}</span>'
                f'</div>\n'
            )
        phases_html = (
            f'<div class="section"><div class="sec-title">Phases</div>{phase_rows}</div>'
            if phase_rows else ""
        )

        other_rows = ""
        for st in e["otherSubtasks"][:15]:
            pc, pl = pill(st["pct"])
            other_rows += (
                f'<div class="sub-row">'
                f'<span class="sub-name" title="{esc(st["name"])}">{esc(st["name"])}</span>'
                f'<span class="pill {pc}">{pl}</span>'
                f'<span class="sub-pct" style="color:{bar_color(st["pct"])}">{st["pct"]}%</span>'
                f'<span class="sub-eff">{fmt(st["plannedHrs"])} / {fmt(st["spentHrs"])}</span>'
                f'</div>\n'
            )
        if len(e["otherSubtasks"]) > 15:
            other_rows += (
                f'<div class="more">+{len(e["otherSubtasks"]) - 15} more…</div>'
            )
        others_html = (
            f'<div class="section"><div class="sec-title">Other Subtasks</div>{other_rows}</div>'
            if other_rows else ""
        )

        cards.append(
            f'<div class="card">'
            f'<div class="card-head">'
            f'<span class="ekey">{esc(e["key"])}</span>'
            f'<span class="etitle">{esc(e["title"])}</span>'
            f'</div>'
            f'<div class="epct-row">'
            f'<span class="epct" style="color:{col}">{pct}%</span>'
            f'<div class="bar-wrap"><div class="bar-track">'
            f'<div class="bar-fill" style="width:{pct}%;background:{col}"></div>'
            f'</div></div>'
            f'</div>'
            f'<div class="eff-tag">{fmt(e["epicPlanned"])} planned &middot; {fmt(e["epicSpent"])} logged</div>'
            f'{phases_html}{others_html}'
            f'</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>TMS02 – Epic Dashboard</title>
<style>
:root{{--bg:#0f1117;--sur:#1a1d27;--sur2:#22263a;--bdr:#2e3250;
      --acc:#4f8ef7;--acc2:#7c5ce4;--grn:#00c48c;--ylw:#ffc542;
      --org:#ff9f43;--txt:#e2e8f0;--mut:#8892a4;--r:10px;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--txt);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;}}
/* header */
.hdr{{background:var(--sur);border-bottom:1px solid var(--bdr);
      padding:14px 24px;display:flex;align-items:center;gap:12px;}}
.hdr h1{{font-size:18px;font-weight:700;}}
.badge{{background:var(--acc);color:#fff;font-size:11px;font-weight:700;
        padding:2px 8px;border-radius:20px;}}
.dot{{width:8px;height:8px;border-radius:50%;background:var(--grn);
      box-shadow:0 0 6px var(--grn);margin-left:auto;}}
.ts{{font-size:11px;color:var(--mut);}}
/* main */
.main{{padding:20px 24px;display:flex;flex-direction:column;gap:20px;}}
/* overall */
.overall{{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r);padding:20px 24px;}}
.overall h2{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;
             color:var(--mut);margin-bottom:12px;}}
.ov-row{{display:flex;align-items:center;gap:16px;}}
.ov-pct{{font-size:36px;font-weight:800;color:var(--acc);min-width:80px;}}
.ov-bar-wrap{{flex:1;}}
.ov-track{{background:var(--sur2);border-radius:6px;height:12px;overflow:hidden;}}
.ov-fill{{height:100%;border-radius:6px;background:linear-gradient(90deg,var(--acc),var(--acc2));}}
.ov-lbl{{font-size:12px;color:var(--mut);margin-top:5px;}}
/* grid */
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;}}
/* card */
.card{{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r);overflow:hidden;}}
.card-head{{padding:14px 16px 10px;border-bottom:1px solid var(--bdr);
            display:flex;align-items:flex-start;gap:10px;}}
.ekey{{font-size:11px;font-weight:700;color:var(--acc);
       background:rgba(79,142,247,.12);padding:2px 7px;border-radius:4px;white-space:nowrap;}}
.etitle{{font-size:14px;font-weight:600;line-height:1.35;}}
.epct-row{{display:flex;align-items:center;gap:10px;padding:10px 16px 0;}}
.epct{{font-size:24px;font-weight:800;min-width:56px;}}
.bar-wrap{{flex:1;}}
.bar-track{{background:var(--sur2);border-radius:4px;height:8px;overflow:hidden;}}
.bar-fill{{height:100%;border-radius:4px;}}
.eff-tag{{font-size:11px;color:var(--mut);padding:4px 16px 10px;}}
/* sections */
.section{{padding:0 16px 12px;}}
.sec-title{{font-size:10px;text-transform:uppercase;letter-spacing:.07em;
            color:var(--mut);margin:8px 0 6px;}}
/* phase row */
.phase-row{{display:flex;align-items:center;gap:8px;margin-bottom:6px;}}
.phase-name{{font-size:12px;min-width:130px;}}
.phase-bar-track{{flex:1;background:var(--sur2);border-radius:3px;height:6px;overflow:hidden;}}
.phase-bar-fill{{height:100%;border-radius:3px;}}
.phase-pct{{font-size:12px;font-weight:600;min-width:36px;text-align:right;}}
.phase-eff{{font-size:11px;color:var(--mut);min-width:55px;text-align:right;}}
/* subtask row */
.sub-row{{display:flex;align-items:center;gap:6px;margin-bottom:5px;font-size:12px;}}
.sub-name{{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.pill{{font-size:10px;font-weight:600;padding:1px 6px;border-radius:10px;white-space:nowrap;}}
.pill-ns{{background:rgba(136,146,164,.18);color:var(--mut);}}
.pill-ip{{background:rgba(255,197,66,.15);color:var(--ylw);}}
.pill-ur{{background:rgba(255,159,67,.15);color:var(--org);}}
.pill-ac{{background:rgba(0,196,140,.15);color:var(--grn);}}
.sub-pct{{font-size:11px;font-weight:600;min-width:30px;text-align:right;}}
.sub-eff{{font-size:11px;color:var(--mut);min-width:80px;text-align:right;}}
.more{{font-size:11px;color:var(--mut);padding:2px 0;}}
/* footer */
.ftr{{background:var(--sur);border-top:1px solid var(--bdr);
      padding:10px 24px;font-size:11px;color:var(--mut);text-align:center;}}
</style>
</head>
<body>
<div class="hdr">
  <div>
    <div style="font-size:11px;color:var(--mut);margin-bottom:2px">PROJECT</div>
    <h1>TMS02 — Epic Progress Dashboard</h1>
  </div>
  <span class="badge">JIRA</span>
  <span class="dot"></span>
  <span class="ts">Auto-generated &middot; {generated_at}</span>
</div>
<div class="main">
  <div class="overall">
    <h2>Overall Project Progress</h2>
    <div class="ov-row">
      <div class="ov-pct">{overall}%</div>
      <div class="ov-bar-wrap">
        <div class="ov-track"><div class="ov-fill" style="width:{overall}%"></div></div>
        <div class="ov-lbl">{fmt(total_planned)} planned across {len(data)} epics</div>
      </div>
    </div>
  </div>
  <div class="grid">
    {"".join(cards)}
  </div>
</div>
<div class="ftr">Auto-refreshed every 30 min via GitHub Actions &middot; TMS02 &middot; dcoretech.atlassian.net</div>
</body>
</html>"""

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Fetching JIRA data...")
    data = build_data()
    print(f"  {len(data)} epics found")
    ts   = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    html = render_html(data, ts)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("  index.html written")
