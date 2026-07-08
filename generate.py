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

# Top-level dropdown groups → tabs within each group.
# groupKeywords: if ANY keyword is found in subtask name, it belongs to this group.
# tabs: checked in order; first matching tab wins; keywords=None is the catch-all.
PHASE_GROUPS = [
    {
        "label": "Requirements",
        "groupKeywords": ["requirements"],
        "tabs": [
            {"label": "Requirements", "keywords": ["requirements"]},
        ],
    },
    {
        "label": "Design",
        "groupKeywords": ["design"],
        "tabs": [
            {"label": "Screen Design", "keywords": ["screen design"]},
            {"label": "API Design",    "keywords": ["api design"]},
            {"label": "Common",        "keywords": None},
        ],
    },
    {
        "label": "Development",
        "groupKeywords": ["development"],
        "tabs": [
            {"label": "API Development",    "keywords": ["api development"]},
            {"label": "Screen Development", "keywords": ["development"]},
            {"label": "Common",             "keywords": None},
        ],
    },
    {
        "label": "Testing",
        "groupKeywords": ["testing"],
        "tabs": [
            {"label": "IT Testing", "keywords": ["it testing"]},
            {"label": "Common",     "keywords": None},
        ],
    },
]

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

def matched_phase_tab(name):
    """Return (phaseGroup label, phaseTab label) for this subtask name, or (None, None)."""
    nl = name.lower()
    for group in PHASE_GROUPS:
        if not any(k.lower() in nl for k in group["groupKeywords"]):
            continue
        for tab in group["tabs"]:
            if tab["keywords"] is None:
                return group["label"], "Common"
            if any(k.lower() in nl for k in tab["keywords"]):
                return group["label"], tab["label"]
        return group["label"], "Common"
    return None, None

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
                    pg, pt = matched_phase_tab(sf["summary"])
                    subs.append({
                        "key":        ref["key"],
                        "name":       sf["summary"],
                        "status":     sf["status"]["name"],
                        "group":      grp,
                        "phaseGroup": pg,
                        "phaseTab":   pt,
                        "plannedHrs": hrs(sf.get("timeoriginalestimate")),
                        "spentHrs":   hrs(sf.get("timespent")),
                    })
                except Exception:
                    pass

            # No subtasks → treat task itself as the leaf item
            if not subs:
                grp = status_group(tf["status"]["name"])
                pg, pt = matched_phase_tab(tf["summary"])
                subs.append({
                    "key":        task["key"],
                    "name":       tf["summary"],
                    "status":     tf["status"]["name"],
                    "group":      grp,
                    "phaseGroup": pg,
                    "phaseTab":   pt,
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

/* ── dropdown bar ── */
.filter-bar{padding:14px 24px;display:flex;align-items:center;gap:12px;
            background:var(--sur);border-bottom:1px solid var(--bdr);}
.filter-bar label{font-size:12px;color:var(--mut);font-weight:600;
                  text-transform:uppercase;letter-spacing:.06em;}
select#phaseSelect{
  background:var(--sur2);color:var(--txt);border:1px solid var(--bdr);
  border-radius:6px;padding:7px 32px 7px 12px;font-size:13px;
  appearance:none;-webkit-appearance:none;cursor:pointer;outline:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%238892a4' d='M0 0l6 8 6-8z'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 10px center;min-width:220px;
}
select#phaseSelect:focus{border-color:var(--acc);}

/* ── tab bar ── */
.tab-bar{
  display:flex;gap:0;
  background:var(--sur);border-bottom:2px solid var(--bdr);
  padding:0 24px;overflow-x:auto;
}
.tab-btn{
  padding:11px 22px;font-size:12px;font-weight:600;
  color:var(--mut);cursor:pointer;border:none;background:transparent;
  border-bottom:2px solid transparent;margin-bottom:-2px;
  text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;
  transition:color .15s,border-color .15s;
}
.tab-btn:hover{color:var(--txt);}
.tab-btn.active{color:var(--acc);border-bottom-color:var(--acc);}

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

/* ── hours summary boxes ── */
.hrs-row{display:flex;gap:0;border-top:1px solid var(--bdr);background:var(--bg);}
.hrs-box{
  flex:1;padding:10px 14px;border-right:1px solid var(--bdr);
  border-left:3px solid transparent;
}
.hrs-box:last-child{border-right:none;}
.hrs-box-label{font-size:10px;font-weight:700;text-transform:uppercase;
               letter-spacing:.08em;margin-bottom:5px;}
.hrs-box-value{font-size:20px;font-weight:800;line-height:1;}
.hrs-todo   {border-left-color:var(--mut);}
.hrs-todo   .hrs-box-label{color:var(--mut);}
.hrs-todo   .hrs-box-value{color:var(--txt);}
.hrs-ip     {border-left-color:var(--ylw);}
.hrs-ip     .hrs-box-label{color:var(--ylw);}
.hrs-ip     .hrs-box-value{color:var(--txt);}
.hrs-review {border-left-color:var(--org);}
.hrs-review .hrs-box-label{color:var(--org);}
.hrs-review .hrs-box-value{color:var(--txt);}
.hrs-done   {border-left-color:var(--grn);}
.hrs-done   .hrs-box-label{color:var(--grn);}
.hrs-done   .hrs-box-value{color:var(--txt);}
.hrs-total  {border-left-color:var(--acc);}
.hrs-total  .hrs-box-label{color:var(--acc);}
.hrs-total  .hrs-box-value{color:var(--txt);}

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
  max-width:100%;cursor:default;
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
.stag-todo      {background:rgba(136,146,164,.18);color:var(--mut);}
.stag-inprogress{background:rgba(255,197,66,.15);color:var(--ylw);}
.stag-review    {background:rgba(255,159,67,.15);color:var(--org);}
.stag-done      {background:rgba(0,196,140,.15);color:var(--grn);}

/* ── footer ── */
.ftr{background:var(--sur);border-top:1px solid var(--bdr);
     padding:10px 24px;font-size:11px;color:var(--mut);text-align:center;}

/* ── no-data ── */
.no-data{padding:20px;text-align:center;color:var(--mut);font-size:13px;}

/* ── sub-group sections (PM-05 etc.) ── */
.subgroup-list{display:flex;flex-direction:column;gap:0;}
.subgroup-section{border-top:1px solid var(--bdr);}
.subgroup-hdr{
  padding:10px 18px;background:var(--sur2);
  display:flex;align-items:center;gap:10px;
}
.subgroup-name{font-size:12px;font-weight:700;text-transform:uppercase;
               letter-spacing:.06em;color:var(--txt);flex:1;}
.subgroup-pct{font-size:12px;font-weight:700;padding:2px 10px;
              border-radius:12px;background:var(--sur);white-space:nowrap;}
.subgroup-pbar{width:80px;}

@media(max-width:700px){
  .status-grid{grid-template-columns:repeat(2,1fr);}
  .status-col:nth-child(2){border-right:none;}
  .status-col:nth-child(3){border-top:1px solid var(--bdr);}
  .tab-btn{padding:10px 14px;font-size:11px;}
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
<div id="tab-bar-wrap"></div>

<div class="main" id="main"></div>

<script>
const DATA         = %%DATA_JSON%%;
const PHASE_GROUPS = %%PHASE_GROUPS_JSON%%;

// Track active tab per group; default to first tab
const currentTabs = {};
PHASE_GROUPS.forEach(g => { currentTabs[g.label] = g.tabs[0].label; });

// ── Utilities ────────────────────────────────────────────────────────────────

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
function fmtHrs(h){
  if(!h||h===0) return '0h';
  return h<1 ? Math.round(h*60)+'m' : (Math.round(h*10)/10)+'h';
}
function sumHrs(subs){ return subs.reduce((a,s)=>a+(s.plannedHrs||0),0); }

// ── PM-05 sub-group config ────────────────────────────────────────────────────

const EPIC_SUBGROUPS = {
  "PM-05": [
    {name:"Master Settings",     keywords:["Master Settings","SM-05"]},
    {name:"Modal Masters",       keywords:["Modal Masters","FN-05M"]},
    {name:"Modal Search Screens",keywords:["Modal Search","FN-05S"]},
  ]
};

function getEpicSubGroups(epic){
  for(const key of Object.keys(EPIC_SUBGROUPS)){
    if(epic.key.includes(key)||epic.title.includes(key)) return EPIC_SUBGROUPS[key];
  }
  return null;
}
function parentMatchesSG(parentName, sg){
  const pn=(parentName||'').toLowerCase();
  return sg.keywords.some(k=>pn.includes(k.toLowerCase()));
}

// ── % calculation ────────────────────────────────────────────────────────────

function epicPct(subs){
  const grpPct={todo:0,inprogress:50,review:80,done:100};
  if(!subs.length) return 0;
  const sum=subs.reduce((a,s)=>a+(grpPct[s.group]||0),0);
  return Math.round(sum/subs.length);
}

// ── Status columns builder ────────────────────────────────────────────────────

function buildStatusCols(subs){
  const COLS=[
    {key:'todo',      label:'To-Do'},
    {key:'inprogress',label:'In Progress'},
    {key:'review',    label:'Review'},
    {key:'done',      label:'Done'},
  ];
  const groups={todo:[],inprogress:[],review:[],done:[]};
  subs.forEach(s=>{ (groups[s.group]=groups[s.group]||[]).push(s); });
  let html='';
  COLS.forEach(c=>{
    const items=groups[c.key]||[];
    const seen=new Set();
    let chips='';
    items.forEach(s=>{
      if(!seen.has(s.parentKey)){
        seen.add(s.parentKey);
        chips+=`<div class="task-chip" title="${esc(s.parentName)}">${esc(s.parentName)}</div>`;
      }
    });
    html+=`
      <div class="status-col ${groupOf(c.key)}">
        <div class="status-head">
          <span class="status-dot"></span>
          <span class="status-label">${c.label}</span>
        </div>
        <div class="status-count">${items.length}</div>
        <div class="task-list">${chips||'<span style="font-size:11px;color:var(--mut)">—</span>'}</div>
      </div>`;
  });
  return html;
}

// ── Hours boxes builder ───────────────────────────────────────────────────────

function buildHrsRow(subs, showTotal=false){
  const groups={todo:[],inprogress:[],review:[],done:[]};
  subs.forEach(s=>{ (groups[s.group]=groups[s.group]||[]).push(s); });
  const tH=sumHrs(groups.todo||[]);
  const iH=sumHrs(groups.inprogress||[]);
  const rH=sumHrs(groups.review||[]);
  const dH=sumHrs(groups.done||[]);
  const total=tH+iH+rH+dH;
  if(total===0&&!showTotal) return '';
  let boxes=`
    <div class="hrs-box hrs-todo"><div class="hrs-box-label">To-Do</div><div class="hrs-box-value">${fmtHrs(tH)}</div></div>
    <div class="hrs-box hrs-ip"><div class="hrs-box-label">In Progress</div><div class="hrs-box-value">${fmtHrs(iH)}</div></div>
    <div class="hrs-box hrs-review"><div class="hrs-box-label">Review</div><div class="hrs-box-value">${fmtHrs(rH)}</div></div>
    <div class="hrs-box hrs-done"><div class="hrs-box-label">Done</div><div class="hrs-box-value">${fmtHrs(dH)}</div></div>`;
  if(showTotal){
    boxes+=`<div class="hrs-box hrs-total"><div class="hrs-box-label">Total</div><div class="hrs-box-value">${fmtHrs(total)}</div></div>`;
  }
  return `<div class="hrs-row">${boxes}</div>`;
}

// ── Tab bar ──────────────────────────────────────────────────────────────────

function renderTabBar(grpLabel){
  const group = PHASE_GROUPS.find(g=>g.label===grpLabel);
  const wrap  = document.getElementById('tab-bar-wrap');
  if(!group || group.tabs.length<=1){
    wrap.innerHTML='';
    return;
  }
  const activeTab = currentTabs[grpLabel];
  const btns = group.tabs.map(t=>`
    <button class="tab-btn ${t.label===activeTab?'active':''}"
            data-group="${esc(grpLabel)}" data-tab="${esc(t.label)}">${esc(t.label)}</button>
  `).join('');
  wrap.innerHTML=`<div class="tab-bar">${btns}</div>`;
}

// Delegated tab click handler
document.getElementById('tab-bar-wrap').addEventListener('click', e=>{
  const btn=e.target.closest('.tab-btn');
  if(!btn) return;
  currentTabs[btn.dataset.group]=btn.dataset.tab;
  renderTabBar(btn.dataset.group);
  renderMain(btn.dataset.group, btn.dataset.tab);
});

// ── Main content render ───────────────────────────────────────────────────────

function renderMain(grpLabel, activeTab){
  let epicCards='';
  let allTabSubs=[];

  DATA.forEach(epic=>{
    const allSubs=[];
    epic.tasks.forEach(task=>{
      task.subtasks.forEach(sub=>{
        if(sub.phaseGroup===grpLabel && sub.phaseTab===activeTab){
          allSubs.push({...sub, parentName:task.name, parentKey:task.key});
          allTabSubs.push(sub);
        }
      });
    });

    const total=allSubs.length;
    const pct=epicPct(allSubs);
    const col=barColor(pct);

    if(total===0){
      epicCards+=`
        <div class="epic-card">
          <div class="epic-hdr">
            <span class="ekey">${esc(epic.key)}</span>
            <span class="etitle">${esc(epic.title)}</span>
            <span class="epic-pct-badge" style="color:var(--mut)">No data</span>
          </div>
          <div class="no-data">No <strong>${esc(activeTab)}</strong> subtasks found in this epic.</div>
        </div>`;
      return;
    }

    const subGroups=getEpicSubGroups(epic);
    let bodyHtml='';

    if(subGroups){
      let sgSections='';
      subGroups.forEach(sg=>{
        const sgSubs=allSubs.filter(s=>parentMatchesSG(s.parentName,sg));
        const sgPct=epicPct(sgSubs);
        const sgCol=barColor(sgPct);
        sgSections+=`
          <div class="subgroup-section">
            <div class="subgroup-hdr">
              <span class="subgroup-name">${esc(sg.name)}</span>
              <span class="subgroup-pct" style="color:${sgCol}">${sgPct}%</span>
              <div class="subgroup-pbar">
                <div class="pbar-track">
                  <div class="pbar-fill" style="width:${sgPct}%;background:${sgCol}"></div>
                </div>
              </div>
            </div>
            ${sgSubs.length
              ?`<div class="status-grid">${buildStatusCols(sgSubs)}</div>${buildHrsRow(sgSubs)}`
              :`<div class="no-data" style="padding:10px 18px;text-align:left">
                  No <strong>${esc(activeTab)}</strong> subtasks in this sub-group.</div>`}
          </div>`;
      });
      bodyHtml=`<div class="subgroup-list">${sgSections}</div>`;
    } else {
      bodyHtml=`<div class="status-grid">${buildStatusCols(allSubs)}</div>${buildHrsRow(allSubs)}`;
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
        ${bodyHtml}
      </div>`;
  });

  // Project-level summary
  const projPct=epicPct(allTabSubs);
  const projCol=barColor(projPct);
  const projSection=`
    <div class="project-summary">
      <h2>Project Completion — ${esc(grpLabel)} › ${esc(activeTab)}</h2>
      <div class="proj-row">
        <div class="proj-pct" style="color:${projCol}">${projPct}%</div>
        <div class="proj-bar-wrap">
          <div class="proj-track">
            <div class="proj-fill" style="width:${projPct}%;background:${projCol}"></div>
          </div>
          <div class="proj-lbl">${allTabSubs.length} "${esc(activeTab)}" subtasks across ${DATA.length} epics</div>
        </div>
      </div>
      ${buildHrsRow(allTabSubs, true)}
    </div>`;

  // Uncategorized subtasks (phaseGroup === null)
  const uncatRows=[];
  DATA.forEach(epic=>{
    epic.tasks.forEach(task=>{
      task.subtasks.forEach(sub=>{
        if(sub.phaseGroup===null||sub.phaseGroup===undefined){
          uncatRows.push({epicTitle:epic.title,epicKey:epic.key,
                          taskName:task.name,taskKey:task.key,...sub});
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
        <td title="${esc(r.taskName)}">${esc(r.taskName)}</td>
        <td title="${esc(r.name)}">${esc(r.name)}</td>
        <td>${esc(r.status)}</td>
        <td><span class="${stagClass(r.group)}">${groupLabel(r.group)}</span></td>
      </tr>`;
    });
    uncatHtml=`
      <div class="uncat-section">
        <h2>Other Subtasks (not matching any phase category)</h2>
        <table class="uncat-table">
          <thead><tr>
            <th>Epic</th><th>Parent Task</th><th>Subtask</th>
            <th>JIRA Status</th><th>Group</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  document.getElementById('main').innerHTML=epicCards+projSection+uncatHtml;
}

// ── Top-level render ──────────────────────────────────────────────────────────

function render(){
  const grpLabel  = document.getElementById('phaseSelect').value;
  const activeTab = currentTabs[grpLabel];
  renderTabBar(grpLabel);
  renderMain(grpLabel, activeTab);
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
        f'<option value="{g["label"]}">{g["label"]}</option>'
        for g in PHASE_GROUPS
    )

    # Pass only label+tabs to JS (no server-side keywords needed client-side)
    pg_for_js = [
        {"label": g["label"], "tabs": [{"label": t["label"]} for t in g["tabs"]]}
        for g in PHASE_GROUPS
    ]

    html = (HTML
            .replace("%%GENERATED_AT%%", ts)
            .replace("%%PHASE_OPTIONS%%", phase_options)
            .replace("%%DATA_JSON%%", json.dumps(data, ensure_ascii=False))
            .replace("%%PHASE_GROUPS_JSON%%", json.dumps(pg_for_js, ensure_ascii=False)))

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("  index.html written")
