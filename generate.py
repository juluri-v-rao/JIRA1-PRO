#!/usr/bin/env python3
"""Fetches TMS02 JIRA data and generates an interactive phase-filter dashboard."""

import os, json, base64, urllib.request, datetime

DOMAIN  = os.environ["JIRA_DOMAIN"]
EMAIL   = os.environ["JIRA_EMAIL"]
TOKEN   = os.environ["JIRA_TOKEN"]
PROJECT = os.environ.get("JIRA_PROJECT", "TMS02")

AUTH    = base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
BASE    = f"https://{DOMAIN}/rest/api/3"
HEADERS = {"Authorization": f"Basic {AUTH}", "Accept": "application/json",
           "Content-Type": "application/json"}

PHASES = ["Requirements", "Screen Design", "API Development", "Development", "IT Testing"]

# ── HTTP ──────────────────────────────────────────────────────────────────────

def _req(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(f"{BASE}/{path}", data=data,
                                  headers=HEADERS, method=method)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def get(path):        return _req("GET",  path)
def post(path, body): return _req("POST", path, body)

def search(jql, fields):
    issues, token = [], None
    while True:
        body = {"jql": jql, "fields": fields, "maxResults": 100}
        if token:
            body["nextPageToken"] = token
        resp  = post("search/jql", body)
        batch = resp.get("issues", [])
        issues += batch
        token  = resp.get("nextPageToken")
        if not token or not batch:
            break
    return issues

# ── Helpers ───────────────────────────────────────────────────────────────────

def hrs(s): return round((s or 0) / 3600, 2)

def status_group(name):
    nl = (name or "").lower().strip()
    if any(x in nl for x in ["done", "closed", "resolved", "approval complete", "承認完了"]):
        return "done"
    if any(x in nl for x in ["review", "内部レビュー中"]):
        return "review"
    if any(x in nl for x in ["progress", "作成中"]):
        return "inprogress"
    return "todo"

GROUP_PCT = {"todo": 0, "inprogress": 50, "review": 80, "done": 100}

def matched_phase(name):
    """Return the phase this subtask belongs to, or None."""
    nl = name.lower()
    for ph in PHASES:
        if ph.lower() in nl:
            return ph
    return None

# ── Data fetch ────────────────────────────────────────────────────────────────

def build_data():
    epics = search(
        f"project={PROJECT} AND issuetype=Epic ORDER BY created ASC",
        ["summary", "status"],
    )

    result = []
    for epic in epics:
        ek = epic["key"]
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

        task_list = []
        for task in tasks:
            tf = task["fields"]
            sub_refs = tf.get("subtasks") or []
            subs = []

            for ref in sub_refs:
                try:
                    d  = get(f"issue/{ref['key']}?fields=summary,status,timeoriginalestimate,timespent")
                    sf = d["fields"]
                    grp = status_group(sf["status"]["name"])
                    subs.append({
                        "key":        ref["key"],
                        "name":       sf["summary"],
                        "status":     sf["status"]["name"],
                        "group":      grp,
                        "phase":      matched_phase(sf["summary"]),
                        "plannedHrs": hrs(sf.get("timeoriginalestimate")),
                        "spentHrs":   hrs(sf.get("timespent")),
                    })
                except Exception:
                    pass

            # No subtasks → treat task itself as the leaf item
            if not subs:
                grp = status_group(tf["status"]["name"])
                subs.append({
                    "key":        task["key"],
                    "name":       tf["summary"],
                    "status":     tf["status"]["name"],
                    "group":      grp,
                    "phase":      matched_phase(tf["summary"]),
                    "plannedHrs": hrs(tf.get("timeoriginalestimate")),
                    "spentHrs":   hrs(tf.get("timespent")),
                })

            task_list.append({
                "key":      task["key"],
                "name":     tf["summary"],
                "status":   tf["status"]["name"],
                "subtasks": subs,
            })

        result.append({
            "key":   ek,
            "title": epic["fields"]["summary"],
            "tasks": task_list,
        })

    return result

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>TMS02 – Phase Dashboard</title>
<style>
:root{
  --bg:#0f1117;--sur:#1a1d27;--sur2:#22263a;--bdr:#2e3250;
  --acc:#4f8ef7;--grn:#00c48c;--ylw:#ffc542;--org:#ff9f43;--red:#ff6b6b;
  --txt:#e2e8f0;--mut:#8892a4;--r:10px;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--txt);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;min-height:100vh;}

/* ── header ── */
.hdr{background:var(--sur);border-bottom:1px solid var(--bdr);
     padding:14px 24px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;}
.hdr-left{display:flex;flex-direction:column;}
.hdr-sub{font-size:11px;color:var(--mut);}
.hdr-title{font-size:18px;font-weight:700;}
.badge{background:var(--acc);color:#fff;font-size:11px;font-weight:700;
       padding:2px 8px;border-radius:20px;}
.dot{width:8px;height:8px;border-radius:50%;background:var(--grn);
     box-shadow:0 0 6px var(--grn);}
.ts{font-size:11px;color:var(--mut);margin-left:auto;}

/* ── dropdown ── */
.filter-bar{padding:16px 24px;display:flex;align-items:center;gap:12px;
            background:var(--sur);border-bottom:1px solid var(--bdr);}
.filter-bar label{font-size:12px;color:var(--mut);font-weight:600;
                  text-transform:uppercase;letter-spacing:.06em;}
select#phaseSelect{
  background:var(--sur2);color:var(--txt);border:1px solid var(--bdr);
  border-radius:6px;padding:7px 32px 7px 12px;font-size:13px;
  appearance:none;-webkit-appearance:none;cursor:pointer;outline:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%238892a4' d='M0 0l6 8 6-8z'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 10px center;min-width:200px;
}
select#phaseSelect:focus{border-color:var(--acc);}

/* ── main ── */
.main{padding:20px 24px;display:flex;flex-direction:column;gap:16px;}

/* ── epic card ── */
.epic-card{background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r);overflow:hidden;}
.epic-hdr{
  padding:14px 18px;border-bottom:1px solid var(--bdr);
  display:flex;align-items:center;gap:12px;
}
.ekey{font-size:11px;font-weight:700;color:var(--acc);
      background:rgba(79,142,247,.12);padding:2px 8px;border-radius:4px;white-space:nowrap;}
.etitle{font-size:14px;font-weight:600;flex:1;}
.epic-pct-badge{
  font-size:13px;font-weight:700;padding:4px 12px;border-radius:20px;
  background:var(--sur2);white-space:nowrap;
}
.pbar-wrap{width:120px;}
.pbar-track{background:var(--sur2);border-radius:4px;height:6px;overflow:hidden;}
.pbar-fill{height:100%;border-radius:4px;}

/* ── status columns ── */
.status-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:0;}
.status-col{padding:14px 16px;border-right:1px solid var(--bdr);}
.status-col:last-child{border-right:none;}
.status-head{display:flex;align-items:center;gap:8px;margin-bottom:10px;}
.status-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;}
.status-label{font-size:11px;font-weight:600;text-transform:uppercase;
              letter-spacing:.06em;color:var(--mut);}
.status-count{font-size:28px;font-weight:800;line-height:1;margin-bottom:10px;}
.task-list{display:flex;flex-direction:column;gap:4px;}
.task-chip{
  font-size:11px;color:var(--txt);background:var(--sur2);
  border:1px solid var(--bdr);border-radius:4px;
  padding:3px 7px;line-height:1.4;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  max-width:100%;
  cursor:default;
}

/* status colours */
.col-todo   .status-dot{background:var(--mut);}
.col-todo   .status-count{color:var(--mut);}
.col-ip     .status-dot{background:var(--ylw);}
.col-ip     .status-count{color:var(--ylw);}
.col-review .status-dot{background:var(--org);}
.col-review .status-count{color:var(--org);}
.col-done   .status-dot{background:var(--grn);}
.col-done   .status-count{color:var(--grn);}

/* ── project summary ── */
.project-summary{
  background:var(--sur);border:1px solid var(--bdr);border-radius:var(--r);
  padding:20px 24px;
}
.project-summary h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;
                    color:var(--mut);margin-bottom:14px;}
.proj-row{display:flex;align-items:center;gap:16px;}
.proj-pct{font-size:40px;font-weight:800;color:var(--acc);min-width:90px;}
.proj-bar-wrap{flex:1;}
.proj-track{background:var(--sur2);border-radius:6px;height:12px;overflow:hidden;}
.proj-fill{height:100%;border-radius:6px;
           background:linear-gradient(90deg,var(--acc),#7c5ce4);}
.proj-lbl{font-size:12px;color:var(--mut);margin-top:6px;}

/* ── uncategorized ── */
.uncat-section{background:var(--sur);border:1px solid var(--bdr);
               border-radius:var(--r);padding:20px 24px;}
.uncat-section h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;
                  color:var(--mut);margin-bottom:14px;}
.uncat-table{width:100%;border-collapse:collapse;}
.uncat-table th{text-align:left;font-size:11px;font-weight:600;
                text-transform:uppercase;letter-spacing:.06em;
                color:var(--mut);padding:6px 10px;
                border-bottom:1px solid var(--bdr);}
.uncat-table td{padding:7px 10px;font-size:12px;border-bottom:1px solid rgba(46,50,80,.5);}
.uncat-table tr:last-child td{border-bottom:none;}
.stag{font-size:10px;font-weight:600;padding:2px 7px;border-radius:10px;white-space:nowrap;}
.stag-todo    {background:rgba(136,146,164,.18);color:var(--mut);}
.stag-inprogress{background:rgba(255,197,66,.15);color:var(--ylw);}
.stag-review  {background:rgba(255,159,67,.15);color:var(--org);}
.stag-done    {background:rgba(0,196,140,.15);color:var(--grn);}

/* ── footer ── */
.ftr{background:var(--sur);border-top:1px solid var(--bdr);
     padding:10px 24px;font-size:11px;color:var(--mut);text-align:center;}

/* ── no-data ── */
.no-data{padding:20px;text-align:center;color:var(--mut);font-size:13px;}
@media(max-width:700px){
  .status-grid{grid-template-columns:repeat(2,1fr);}
  .status-col:nth-child(2){border-right:none;}
  .status-col:nth-child(3){border-top:1px solid var(--bdr);}
}
</style>
</head>
<body>

<div class="hdr">
  <div class="hdr-left">
    <span class="hdr-sub">PROJECT</span>
    <span class="hdr-title">TMS02 — Phase Progress Dashboard</span>
  </div>
  <span class="badge">JIRA</span>
  <span class="dot"></span>
  <span class="ts">Auto-generated · %%GENERATED_AT%%</span>
</div>

<div class="filter-bar">
  <label for="phaseSelect">View Phase:</label>
  <select id="phaseSelect">%%PHASE_OPTIONS%%</select>
</div>

<div class="main" id="main"></div>

<script>
const DATA   = %%DATA_JSON%%;
const PHASES = %%PHASES_JSON%%;

function barColor(p){
  if(p>=100) return '#00c48c';
  if(p>=80)  return '#4f8ef7';
  if(p>=30)  return '#ffc542';
  return '#8892a4';
}
function groupOf(g){
  return {todo:'col-todo',inprogress:'col-ip',review:'col-review',done:'col-done'}[g]||'col-todo';
}
function groupLabel(g){
  return {todo:'To-Do',inprogress:'In Progress',review:'Review',done:'Done'}[g]||g;
}
function stagClass(g){return 'stag stag-'+g;}
function esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
                  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function epicPct(phaseSubs){
  const grpPct={todo:0,inprogress:50,review:80,done:100};
  if(!phaseSubs.length) return 0;
  const sum=phaseSubs.reduce((a,s)=>a+(grpPct[s.group]||0),0);
  return Math.round(sum/phaseSubs.length);
}

function render(){
  const phase = document.getElementById('phaseSelect').value;
  const main  = document.getElementById('main');

  // ── Build epic cards ──────────────────────────────────────────────────────
  let epicCards = '';
  let allPhaseSubs = []; // for project-level %

  DATA.forEach(epic=>{
    // collect subtasks matching this phase, with their parent task info
    const groups={todo:[],inprogress:[],review:[],done:[]};

    epic.tasks.forEach(task=>{
      task.subtasks.forEach(sub=>{
        if(sub.phase===phase){
          groups[sub.group]=groups[sub.group]||[];
          groups[sub.group].push({...sub, parentName:task.name, parentKey:task.key});
          allPhaseSubs.push(sub);
        }
      });
    });

    const total=groups.todo.length+groups.inprogress.length+
                groups.review.length+groups.done.length;

    // flatten phase subs for % calc
    const flatSubs=[...groups.todo,...groups.inprogress,...groups.review,...groups.done];
    const pct=epicPct(flatSubs);
    const col=barColor(pct);

    // status columns
    const COLS=[
      {key:'todo',     label:'To-Do'},
      {key:'inprogress',label:'In Progress'},
      {key:'review',   label:'Review'},
      {key:'done',     label:'Done'},
    ];

    let colsHtml='';
    COLS.forEach(c=>{
      const items=groups[c.key]||[];
      // deduplicate parent tasks shown
      const seen=new Set();
      let chips='';
      items.forEach(s=>{
        if(!seen.has(s.parentKey)){
          seen.add(s.parentKey);
          chips+=`<div class="task-chip" title="${esc(s.parentName)}">${esc(s.parentName)}</div>`;
        }
      });
      colsHtml+=`
        <div class="status-col ${groupOf(c.key)}">
          <div class="status-head">
            <span class="status-dot"></span>
            <span class="status-label">${c.label}</span>
          </div>
          <div class="status-count">${items.length}</div>
          <div class="task-list">${chips||'<span style="font-size:11px;color:var(--mut)">—</span>'}</div>
        </div>`;
    });

    // hide epics with no matching subtasks if zero
    if(total===0){
      epicCards+=`
        <div class="epic-card">
          <div class="epic-hdr">
            <span class="ekey">${esc(epic.key)}</span>
            <span class="etitle">${esc(epic.title)}</span>
            <span class="epic-pct-badge" style="color:var(--mut)">No data</span>
          </div>
          <div class="no-data">No <strong>${esc(phase)}</strong> subtasks found in this epic.</div>
        </div>`;
      return;
    }

    epicCards+=`
      <div class="epic-card">
        <div class="epic-hdr">
          <span class="ekey">${esc(epic.key)}</span>
          <span class="etitle">${esc(epic.title)}</span>
          <span class="epic-pct-badge" style="color:${col}">${pct}%</span>
          <div class="pbar-wrap">
            <div class="pbar-track">
              <div class="pbar-fill" style="width:${pct}%;background:${col}"></div>
            </div>
          </div>
        </div>
        <div class="status-grid">${colsHtml}</div>
      </div>`;
  });

  // ── Project-level summary ────────────────────────────────────────────────
  const projPct=epicPct(allPhaseSubs);
  const projCol=barColor(projPct);
  const projSection=`
    <div class="project-summary">
      <h2>Project Completion — ${esc(phase)}</h2>
      <div class="proj-row">
        <div class="proj-pct" style="color:${projCol}">${projPct}%</div>
        <div class="proj-bar-wrap">
          <div class="proj-track">
            <div class="proj-fill" style="width:${projPct}%;background:${projCol}"></div>
          </div>
          <div class="proj-lbl">${allPhaseSubs.length} "${esc(phase)}" subtasks across ${DATA.length} epics</div>
        </div>
      </div>
    </div>`;

  // ── Uncategorized subtasks ────────────────────────────────────────────────
  const uncatRows=[];
  DATA.forEach(epic=>{
    epic.tasks.forEach(task=>{
      task.subtasks.forEach(sub=>{
        if(sub.phase===null){
          uncatRows.push({epic:epic.title,epicKey:epic.key,
                          task:task.name,taskKey:task.key,
                          ...sub});
        }
      });
    });
  });

  let uncatHtml='';
  if(uncatRows.length){
    let rows='';
    uncatRows.forEach(r=>{
      rows+=`<tr>
        <td>${esc(r.epicKey)}</td>
        <td title="${esc(r.task)}">${esc(r.task)}</td>
        <td title="${esc(r.name)}">${esc(r.name)}</td>
        <td>${esc(r.status)}</td>
        <td><span class="${stagClass(r.group)}">${groupLabel(r.group)}</span></td>
      </tr>`;
    });
    uncatHtml=`
      <div class="uncat-section">
        <h2>Other Subtasks (not in any phase category)</h2>
        <table class="uncat-table">
          <thead><tr>
            <th>Epic</th><th>Parent Task</th><th>Subtask</th>
            <th>JIRA Status</th><th>Group</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  main.innerHTML = epicCards + projSection + uncatHtml;
}

document.getElementById('phaseSelect').addEventListener('change', render);
render();
</script>

<div class="ftr">Auto-refreshed every 30 min via GitHub Actions &middot; TMS02 &middot; dcoretech.atlassian.net</div>
</body>
</html>"""

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Fetching JIRA data...")
    data = build_data()
    print(f"  {len(data)} epics found")

    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    phase_options = "\n".join(
        f'<option value="{ph}">{ph}</option>' for ph in PHASES
    )

    html = (HTML
            .replace("%%GENERATED_AT%%", ts)
            .replace("%%PHASE_OPTIONS%%", phase_options)
            .replace("%%DATA_JSON%%", json.dumps(data, ensure_ascii=False))
            .replace("%%PHASES_JSON%%", json.dumps(PHASES, ensure_ascii=False)))

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("  index.html written")
