#!/usr/bin/env python3
"""Fetches TMS02 JIRA data and generates a static index.html dashboard."""

import os, json, base64, urllib.request, urllib.parse, datetime

DOMAIN  = os.environ["JIRA_DOMAIN"]
EMAIL   = os.environ["JIRA_EMAIL"]
TOKEN   = os.environ["JIRA_TOKEN"]
PROJECT = os.environ.get("JIRA_PROJECT", "TMS02")

AUTH = base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
HEADERS = {"Authorization": f"Basic {AUTH}", "Accept": "application/json"}

COMMON_PHASES = ["Screen Design", "Requirements", "API Design"]
STATUS_PCT = {
    "not started": 0, "未着手": 0,
    "in progress": 30, "作成中": 30,
    "under review": 80, "内部レビュー中": 80,
    "approval complete": 100, "承認完了": 100,
}

def status_pct(name):
    if not name: return 0
    nl = name.lower()
    for k, v in STATUS_PCT.items():
        if k in nl: return v
    if nl in ("done", "closed", "resolved"): return 100
    if nl in ("in progress", "in review"):   return 30
    return 0

def jira_get(path):
    url = f"https://{DOMAIN}/rest/api/3/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def jira_post(path, body):
    url = f"https://{DOMAIN}/rest/api/3/{path}"
    payload = json.dumps(body).encode()
    headers = {**HEADERS, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def fetch_all(jql, fields):
    items, start = [], 0
    while True:
        data = jira_post("search/jql", {
            "jql": jql,
            "fields": fields,
            "maxResults": 100,
            "startAt": start,
        })
        items += data["issues"]
        start += len(data["issues"])
        if start >= data["total"] or not data["issues"]:
            break
    return items

def hrs(sec): return (sec or 0) / 3600

def build_data():
    epics = fetch_all(f"project={PROJECT} AND issuetype=Epic ORDER BY created ASC",
                      ["summary", "status", "timeoriginalestimate", "timespent", "subtasks"])
    result = []
    for epic in epics:
        ek = epic["key"]
        task_fields = ["summary", "status", "timeoriginalestimate", "timespent", "subtasks", "parent"]
        tasks = fetch_all(f'project={PROJECT} AND "Epic Link"={ek} AND issuetype not in (Epic,Sub-task)', task_fields)
        if not tasks:
            tasks = fetch_all(f"project={PROJECT} AND parent={ek} AND issuetype not in (Epic,Sub-task)", task_fields)

        task_data = []
        for task in tasks:
            subs = []
            for ref in task["fields"].get("subtasks", []):
                try:
                    d = jira_get(f"issue/{ref['key']}?fields=summary,status,timeoriginalestimate,timespent")
                    subs.append({
                        "key": ref["key"],
                        "name": d["fields"]["summary"],
                        "status": d["fields"]["status"]["name"],
                        "plannedHrs": hrs(d["fields"].get("timeoriginalestimate")),
                        "spentHrs":   hrs(d["fields"].get("timespent")),
                        "pct": status_pct(d["fields"]["status"]["name"]),
                    })
                except Exception:
                    pass
            if not subs:
                subs.append({
                    "key": task["key"],
                    "name": task["fields"]["summary"],
                    "status": task["fields"]["status"]["name"],
                    "plannedHrs": hrs(task["fields"].get("timeoriginalestimate")),
                    "spentHrs":   hrs(task["fields"].get("timespent")),
                    "pct": status_pct(task["fields"]["status"]["name"]),
                })
            tp = sum(s["plannedHrs"] for s in subs)
            tw = sum(s["plannedHrs"] * s["pct"] for s in subs)
            task_data.append({
                "key": task["key"],
                "name": task["fields"]["summary"],
                "plannedHrs": tp or hrs(task["fields"].get("timeoriginalestimate")),
                "spentHrs": sum(s["spentHrs"] for s in subs),
                "pct": tw / tp if tp else status_pct(task["fields"]["status"]["name"]),
                "subtasks": subs,
            })

        ep = sum(t["plannedHrs"] for t in task_data)
        ew = sum(t["plannedHrs"] * t["pct"] for t in task_data)
        epic_pct = ew / ep if ep else status_pct(epic["fields"]["status"]["name"])

        all_subs = [s for t in task_data for s in t["subtasks"]]
        phases, others = {}, []
        for sub in all_subs:
            matched = False
            for ph in COMMON_PHASES:
                if ph.lower() in sub["name"].lower():
                    phases.setdefault(ph, []).append(sub)
                    matched = True; break
            if not matched:
                others.append(sub)

        phase_stats = {}
        for ph, ss in phases.items():
            pl = sum(s["plannedHrs"] for s in ss)
            ws = sum(s["plannedHrs"] * s["pct"] for s in ss)
            phase_stats[ph] = {"pct": ws / pl if pl else 0, "plannedHrs": pl,
                               "spentHrs": sum(s["spentHrs"] for s in ss)}

        result.append({
            "key": ek,
            "title": epic["fields"]["summary"],
            "epicPct": epic_pct,
            "epicPlanned": ep,
            "epicSpent": sum(t["spentHrs"] for t in task_data),
            "phaseStats": phase_stats,
            "otherSubtasks": others,
        })
    return result

def render_html(data, generated_at):
    total_planned = sum(e["epicPlanned"] for e in data)
    total_weighted = sum(e["epicPlanned"] * e["epicPct"] for e in data)
    overall = round(total_planned and total_weighted / total_planned or 0)

    def bar_color(p):
        if p >= 100: return "#00c48c"
        if p >= 80:  return "#4f8ef7"
        if p >= 30:  return "#ffc542"
        return "#8892a4"

    def hrs_display(h):
        d, r = int(h // 8), round(h % 8, 1)
        if d == 0: return f"{r}h"
        if r == 0: return f"{d}d"
        return f"{d}d {r}h"

    def pill_class(p):
        if p == 0:   return "pill-ns"
        if p <= 30:  return "pill-ip"
        if p <= 80:  return "pill-ur"
        return "pill-ac"

    def pill_label(p):
        if p == 0:   return "Not Started"
        if p <= 30:  return "In Progress"
        if p <= 80:  return "Under Review"
        return "Approval Complete"

    def esc(s):
        return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

    cards = []
    for e in data:
        pct = round(e["epicPct"])
        col = bar_color(pct)

        phase_rows = ""
        for ph, s in e["phaseStats"].items():
            pp = round(s["pct"])
            phase_rows += f'''<div class="phase-row">
              <span class="phase-name">{esc(ph)}</span>
              <div class="phase-bar-track"><div class="phase-bar-fill" style="width:{pp}%;background:{bar_color(pp)}"></div></div>
              <span class="phase-pct" style="color:{bar_color(pp)}">{pp}%</span>
              <span class="phase-effort">{hrs_display(s["plannedHrs"])}</span>
            </div>'''
        phases_html = f'<div class="phases-wrap"><div class="phases-title">Phases</div>{phase_rows}</div>' if phase_rows else ""

        other_rows = ""
        for st in e["otherSubtasks"][:15]:
            other_rows += f'''<div class="subtask-row">
              <span class="subtask-name" title="{esc(st["name"])}">{esc(st["name"])}</span>
              <span class="status-pill {pill_class(st["pct"])}">{pill_label(st["pct"])}</span>
              <span class="subtask-pct" style="color:{bar_color(st["pct"])}">{st["pct"]}%</span>
              <span class="subtask-hrs">{hrs_display(st["plannedHrs"])} / {hrs_display(st["spentHrs"])}</span>
            </div>'''
        if len(e["otherSubtasks"]) > 15:
            extra = len(e["otherSubtasks"]) - 15
            other_rows += f'<div style="font-size:11px;color:var(--muted);padding:4px 0">+{extra} more…</div>'
        others_html = f'<div class="other-wrap"><div class="other-title">Other Subtasks</div>{other_rows}</div>' if other_rows else ""

        cards.append(f'''
        <div class="epic-card">
          <div class="epic-header">
            <span class="epic-key">{esc(e["key"])}</span>
            <span class="epic-title">{esc(e["title"])}</span>
          </div>
          <div class="epic-pct-row">
            <span class="epic-pct" style="color:{col}">{pct}%</span>
            <div class="epic-bar-wrap">
              <div class="epic-bar-track"><div class="epic-bar-fill" style="width:{pct}%;background:{col}"></div></div>
            </div>
          </div>
          <div class="effort-tag">{hrs_display(e["epicPlanned"])} planned · {hrs_display(e["epicSpent"])} logged</div>
          {phases_html}{others_html}
        </div>''')

    cards_html = "\n".join(cards)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>TMS02 – Epic Progress Dashboard</title>
  <style>
    :root {{
      --bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2e3250;
      --accent:#4f8ef7;--accent2:#7c5ce4;--green:#00c48c;--yellow:#ffc542;
      --orange:#ff9f43;--text:#e2e8f0;--muted:#8892a4;--radius:10px;
    }}
    *{{box-sizing:border-box;margin:0;padding:0;}}
    body{{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;min-height:100vh;}}
    .header{{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;gap:14px;}}
    .header h1{{font-size:18px;font-weight:700;}}
    .badge{{background:var(--accent);color:#fff;font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;}}
    .status-dot{{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 6px var(--green);margin-left:auto;}}
    .status-text{{font-size:12px;color:var(--muted);}}
    .main{{padding:20px 24px;display:flex;flex-direction:column;gap:20px;}}
    .overall-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px 24px;}}
    .overall-card h2{{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:12px;}}
    .overall-row{{display:flex;align-items:center;gap:16px;}}
    .overall-pct{{font-size:36px;font-weight:800;color:var(--accent);min-width:80px;}}
    .bar-wrap{{flex:1;}}
    .bar-track{{background:var(--surface2);border-radius:6px;height:12px;overflow:hidden;}}
    .bar-fill{{height:100%;border-radius:6px;background:linear-gradient(90deg,var(--accent),var(--accent2));}}
    .bar-label{{font-size:12px;color:var(--muted);margin-top:5px;}}
    .epics-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;}}
    .epic-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;}}
    .epic-header{{padding:14px 16px 10px;border-bottom:1px solid var(--border);display:flex;align-items:flex-start;gap:10px;}}
    .epic-key{{font-size:11px;font-weight:700;color:var(--accent);background:rgba(79,142,247,.12);padding:2px 7px;border-radius:4px;white-space:nowrap;}}
    .epic-title{{font-size:14px;font-weight:600;line-height:1.35;}}
    .epic-pct-row{{display:flex;align-items:center;gap:10px;padding:10px 16px 0;}}
    .epic-pct{{font-size:24px;font-weight:800;min-width:56px;}}
    .epic-bar-wrap{{flex:1;}}
    .epic-bar-track{{background:var(--surface2);border-radius:4px;height:8px;overflow:hidden;}}
    .epic-bar-fill{{height:100%;border-radius:4px;}}
    .effort-tag{{font-size:11px;color:var(--muted);padding:0 16px 10px;margin-top:4px;}}
    .phases-wrap{{padding:0 16px 6px;}}
    .phases-title{{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:10px 0 6px;}}
    .phase-row{{display:flex;align-items:center;gap:8px;margin-bottom:6px;}}
    .phase-name{{font-size:12px;min-width:130px;color:var(--text);}}
    .phase-bar-track{{flex:1;background:var(--surface2);border-radius:3px;height:6px;overflow:hidden;}}
    .phase-bar-fill{{height:100%;border-radius:3px;}}
    .phase-pct{{font-size:12px;font-weight:600;min-width:36px;text-align:right;}}
    .phase-effort{{font-size:11px;color:var(--muted);min-width:60px;text-align:right;}}
    .other-wrap{{padding:0 16px 14px;}}
    .other-title{{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:8px 0 6px;}}
    .subtask-row{{display:flex;align-items:center;gap:6px;margin-bottom:5px;font-size:12px;}}
    .subtask-name{{flex:1;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
    .status-pill{{font-size:10px;font-weight:600;padding:1px 6px;border-radius:10px;white-space:nowrap;}}
    .pill-ns{{background:rgba(136,146,164,.18);color:var(--muted);}}
    .pill-ip{{background:rgba(255,197,66,.15);color:var(--yellow);}}
    .pill-ur{{background:rgba(255,159,67,.15);color:var(--orange);}}
    .pill-ac{{background:rgba(0,196,140,.15);color:var(--green);}}
    .subtask-pct{{font-size:11px;font-weight:600;min-width:30px;text-align:right;}}
    .subtask-hrs{{font-size:11px;color:var(--muted);min-width:70px;text-align:right;}}
    .footer{{background:var(--surface);border-top:1px solid var(--border);padding:10px 24px;font-size:11px;color:var(--muted);text-align:center;}}
  </style>
</head>
<body>
<div class="header">
  <div>
    <div style="font-size:11px;color:var(--muted);margin-bottom:2px;">PROJECT</div>
    <h1>TMS02 — Epic Progress Dashboard</h1>
  </div>
  <span class="badge">JIRA</span>
  <span class="status-dot"></span>
  <span class="status-text">Auto-generated · {generated_at}</span>
</div>
<div class="main">
  <div class="overall-card">
    <h2>Overall Project Progress</h2>
    <div class="overall-row">
      <div class="overall-pct">{overall}%</div>
      <div class="bar-wrap">
        <div class="bar-track"><div class="bar-fill" style="width:{overall}%"></div></div>
        <div class="bar-label">{hrs_display(total_planned)} planned across {len(data)} epics</div>
      </div>
    </div>
  </div>
  <div class="epics-grid">
    {cards_html}
  </div>
</div>
<div class="footer">Auto-refreshed every 30 min via GitHub Actions · TMS02 · dcoretech.atlassian.net</div>
</body>
</html>'''

if __name__ == "__main__":
    print("Fetching JIRA data...")
    data = build_data()
    print(f"  Got {len(data)} epics")
    generated_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    html = render_html(data, generated_at)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("  index.html written successfully")
