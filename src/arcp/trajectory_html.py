"""VIZ(2026-08-15)— trajectory.html 產生器:抄 DeepSeek harness Trajectory 排版。

從 attempts/a*.events.jsonl(rawcli 蒸餾流,含 timestamp+category)渲染
**自足單檔** trajectory.html,與 cclog 的 final.html 並存於 transcript/:

    ┌─ Overview:3 語意泳道時間帶(user/assistant/tool;TTFT 淡段) ─┐
    ├─ ledger(#/事件/內容) ────────┬─ details(Content/Timing 頁籤)─┤
    └──────────────────────────────┴──────────────────────────────┘

抄的八項(research/2026-08-trajectory-viz-comparison.md):3 泳道、token 化
配色(明暗)、TTFT 漸層、opacity 聚焦(未選 0.2/搜尋不中 0.14)、hover 光暈
+500ms tooltip、wheel 錨點縮放+右鍵平移、拖選區間→ledger 聯動(區間外打暗)、
sequence/time 投影切換。純離線 vanilla js、零外部資源;in-flight/末事件不
捏造時長(min 寬)。舊事件檔無 category → fallback emoji 前綴判斷。
"""
from __future__ import annotations

import datetime
import glob
import html
import json
import os
import re

_EMOJI_CAT = (("🔧", "tool"), ("📋", "tool_result"), ("💭", "thinking"))
_LANE = {"user": 0, "text": 1, "thinking": 1, "tool": 2, "tool_result": 2}
_MIN_SPAN_S = 0.35        # 末事件/零時長的最小視覺寬(不捏造長時長)


def _cat_of(ev: dict, text: str) -> str:
    c = ev.get("category")
    if c:
        return c
    if ev.get("source") != "agent":
        return "user"
    for emoji, cat in _EMOJI_CAT:
        if text.startswith(emoji):
            return cat
    return "text"


def _text_of(ev: dict) -> str:
    for b in (ev.get("llm_message") or {}).get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text":
            return b.get("text") or ""
    return ""


def _ts(ev: dict) -> float | None:
    try:
        return datetime.datetime.fromisoformat(ev["timestamp"]).timestamp()
    except (KeyError, ValueError, TypeError):
        return None


def collect(attempts_dir: str) -> list[dict]:
    """掃 a*.events.jsonl → 攤平事件清單(帶 attempt/lane/start/end)。
    span 時長=到同 attempt 下一事件;末事件=min 寬(誠實:不知道就不畫長)。"""
    records: list[dict] = []
    paths = sorted(glob.glob(os.path.join(attempts_dir, "a*.events.jsonl")),
                   key=lambda p: int(re.search(r"a(\d+)\.", p).group(1)))
    for path in paths:
        attempt = int(re.search(r"a(\d+)\.", path).group(1))
        evs = []
        try:
            for line in open(path, encoding="utf-8"):
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = _ts(e)
                if t is None:
                    continue
                txt = _text_of(e)
                evs.append({"t": t, "cat": _cat_of(e, txt), "text": txt})
        except OSError:
            continue
        for i, e in enumerate(evs):
            end = evs[i + 1]["t"] if i + 1 < len(evs) else e["t"] + _MIN_SPAN_S
            records.append({
                "i": len(records), "attempt": attempt,
                "cat": e["cat"], "lane": _LANE.get(e["cat"], 1),
                "start": e["t"], "end": max(end, e["t"] + _MIN_SPAN_S),
                "text": e["text"],
                # TTFT:attempt 首個 agent 事件之前的 user prompt 段(js 端算)
            })
    return records


def render_trajectory(attempts_dir: str, out_path: str,
                      title: str = "trajectory") -> str | None:
    """產 trajectory.html;無事件回 None(不產空檔)。"""
    records = collect(attempts_dir)
    if not records:
        return None
    data = {"title": title, "records": records}
    doc = (_TPL.replace("__DATA__", json.dumps(data, ensure_ascii=False)
                        .replace("</", "<\\/"))
           .replace("__TITLE__", html.escape(title)))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


# ── 模板(自足單檔;__DATA__/__TITLE__ 置換)────────────────────────────── #
_TPL = r"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · trajectory</title>
<style>
/* token 兩層:static→語意 alias(抄 DeepSeek 三層精神;明暗只重映射 alias) */
:root{
  --tj-bg-1:#fff; --tj-bg-2:#fafafa; --tj-border-1:#ececec; --tj-border-2:#ddd;
  --tj-label-1:#1c1c1e; --tj-label-2:#61666b; --tj-label-3:#9aa0a6;
  --tj-user:rgb(65,118,230); --tj-tool:rgb(221,134,41);
  --tj-assist:rgb(132,94,247); --tj-err:rgb(236,19,19); --tj-ok:rgb(34,197,94);
}
@media (prefers-color-scheme: dark){:root:not([data-theme=light]){
  --tj-bg-1:#232324; --tj-bg-2:#2c2c2e; --tj-border-1:#3a3a3c; --tj-border-2:#48484a;
  --tj-label-1:#e8e8ea; --tj-label-2:#cfd3d6; --tj-label-3:#8e9297;
  --tj-user:rgb(103,158,254); --tj-err:rgb(242,90,90);
}}
:root[data-theme=dark]{
  --tj-bg-1:#232324; --tj-bg-2:#2c2c2e; --tj-border-1:#3a3a3c; --tj-border-2:#48484a;
  --tj-label-1:#e8e8ea; --tj-label-2:#cfd3d6; --tj-label-3:#8e9297;
  --tj-user:rgb(103,158,254); --tj-err:rgb(242,90,90);
}
*{box-sizing:border-box}
body{margin:0;font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",
  "Noto Sans TC",sans-serif;background:var(--tj-bg-1);color:var(--tj-label-1);
  height:100vh;display:flex;flex-direction:column;overflow:hidden}
header{flex:none;display:flex;align-items:center;gap:10px;padding:6px 12px;
  border-bottom:1px solid var(--tj-border-1);background:var(--tj-bg-2)}
header h1{font-size:13px;margin:0;font-weight:600}
header .hint{color:var(--tj-label-3);font-size:11px}
header input[type=search]{margin-left:auto;padding:4px 8px;border:1px solid
  var(--tj-border-2);border-radius:6px;background:var(--tj-bg-1);
  color:var(--tj-label-1);font:inherit;width:180px}
.modes{display:flex;border:1px solid var(--tj-border-2);border-radius:6px;overflow:hidden}
.modes button{border:0;background:transparent;color:var(--tj-label-2);
  padding:3px 10px;font:inherit;font-size:11px;cursor:pointer}
.modes button[data-on=true]{background:var(--tj-user);color:#fff}
/* ── Overview(50px 三泳道;抄 Trajectory)── */
#ov{flex:none;position:relative;display:grid;grid-template-columns:52px 1fr;
  height:56px;border-bottom:1px solid var(--tj-border-2);background:var(--tj-bg-2);
  user-select:none}
#ovLabels{position:relative;border-right:1px solid var(--tj-border-1);
  font-size:9px;color:var(--tj-label-3);line-height:1}
#ovLabels span{position:absolute;right:4px;height:8px;display:flex;align-items:center}
#ovLabels span:nth-child(1){top:9px}#ovLabels span:nth-child(2){top:23px}
#ovLabels span:nth-child(3){top:37px}
#track{position:relative;overflow:hidden;cursor:crosshair;touch-action:none}
#track.pan{cursor:grabbing}
.span{position:absolute;height:8px;min-width:2px;border-radius:1.5px;
  top:calc(9px + var(--lane)*14px);opacity:.85}
.span[data-cat=user]{background:var(--tj-user)}
.span[data-cat=text]{background:var(--tj-assist)}
.span[data-cat=thinking]{background:color-mix(in srgb,var(--tj-assist) 55%,var(--tj-bg-2))}
.span[data-cat=tool],.span[data-cat=tool_result]{background:var(--tj-tool)}
.span[data-ttft=true]{background:linear-gradient(to right,
  color-mix(in srgb,var(--tj-assist) 40%,var(--tj-bg-2)) 0 100%)}
.span.dim{opacity:.2}.span.searchdim{opacity:.14}
.span.hov,.span.cur{opacity:1;z-index:2;box-shadow:0 0 0 1px var(--tj-bg-2),
  0 0 0 2px var(--tj-user)}
.turnline{position:absolute;top:0;bottom:0;width:1px;background:var(--tj-border-2)}
.turntag{position:absolute;top:1px;font-size:8px;color:var(--tj-label-3)}
#sel{position:absolute;top:0;bottom:0;background:color-mix(in srgb,var(--tj-user) 12%,transparent);
  box-shadow:-100vw 0 0 100vw color-mix(in srgb,var(--tj-bg-1) 58%,transparent),
  100vw 0 0 100vw color-mix(in srgb,var(--tj-bg-1) 58%,transparent);
  pointer-events:none;display:none}
#sel::before,#sel::after{content:'';position:absolute;top:0;bottom:0;width:3px;
  background:var(--tj-user)}
#sel::before{left:0}#sel::after{right:0}
#hline{position:absolute;top:0;bottom:0;width:2px;background:var(--tj-user);
  pointer-events:none;display:none}
#tip{position:fixed;z-index:9;background:var(--tj-bg-1);border:1px solid
  var(--tj-border-2);border-radius:6px;padding:4px 8px;font-size:11px;
  pointer-events:none;display:none;box-shadow:0 2px 8px rgba(0,0,0,.18);max-width:320px}
/* ── ledger + details ── */
#main{flex:1;display:flex;min-height:0}
#ledger{flex:1;overflow:auto;min-width:0}
table{width:100%;border-collapse:collapse;table-layout:fixed}
th{position:sticky;top:0;background:var(--tj-bg-2);text-align:left;font-size:11px;
  color:var(--tj-label-3);padding:5px 10px;border-bottom:1px solid var(--tj-border-2);
  font-weight:500;z-index:1}
td{padding:4px 10px;border-bottom:1px solid var(--tj-border-1);vertical-align:top}
tr.row{cursor:pointer}
tr.row:hover{background:color-mix(in srgb,var(--tj-user) 6%,transparent)}
tr.row.cur{background:color-mix(in srgb,var(--tj-user) 12%,transparent)}
tr.row.searchdim{opacity:.25}
tr.turnhead td{border-top:2px solid var(--tj-border-2);background:var(--tj-bg-2);
  color:var(--tj-label-3);font-size:11px;padding:3px 10px}
.idx{color:var(--tj-label-3);font-size:11px;font-variant-numeric:tabular-nums}
.chip{display:inline-block;font-size:10px;padding:1px 7px;border-radius:8px;
  color:#fff;line-height:1.5;white-space:nowrap}
.chip[data-cat=user]{background:var(--tj-user)}
.chip[data-cat=text]{background:var(--tj-assist)}
.chip[data-cat=thinking]{background:color-mix(in srgb,var(--tj-assist) 60%,var(--tj-bg-1));
  color:var(--tj-label-1)}
.chip[data-cat=tool],.chip[data-cat=tool_result]{background:var(--tj-tool)}
.prev{color:var(--tj-label-2);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;display:block}
#details{flex:none;position:relative;width:clamp(300px,36%,440px);
  max-width:calc(100% - 260px);display:flex;flex-direction:column;
  border-left:1px solid var(--tj-border-2);background:var(--tj-bg-1)}
#dresize{position:absolute;left:-4px;top:0;bottom:0;width:8px;cursor:col-resize;
  z-index:3}
#dtabs{flex:none;display:flex;gap:2px;height:38px;align-items:center;
  padding:0 10px;border-bottom:1px solid var(--tj-border-1)}
#dtabs button{border:0;background:transparent;color:var(--tj-label-2);
  padding:4px 10px;border-radius:6px;font:inherit;font-size:12px;cursor:pointer}
#dtabs button[data-on=true]{background:color-mix(in srgb,var(--tj-user) 14%,transparent);
  color:var(--tj-label-1)}
#dbody{flex:1;overflow:auto;padding:10px 12px}
#dbody pre{white-space:pre-wrap;word-break:break-word;font:12px/1.55
  ui-monospace,Menlo,monospace;margin:0}
#dbody dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:12px}
#dbody dt{color:var(--tj-label-3)}#dbody dd{margin:0;font-variant-numeric:tabular-nums}
.dempty{color:var(--tj-label-3);font-size:12px;padding:16px;text-align:center}
@media (prefers-reduced-motion: no-preference){.span{transition:opacity .12s}}
</style></head><body>
<header><h1>__TITLE__ · trajectory</h1>
  <div class="modes"><button id="mTime" data-on="true">time</button><button id="mSeq">sequence</button></div>
  <span class="hint">滾輪=縮放 · 左鍵拖=選區間(ledger 聯動)· 右鍵=清除/平移 · 點色塊/列=詳情</span>
  <input id="q" type="search" placeholder="搜尋事件內容…">
</header>
<div id="ov"><div id="ovLabels"><span>user</span><span>agent</span><span>tool</span></div>
  <div id="track"><div id="sel"></div><div id="hline"></div></div></div>
<div id="main">
  <div id="ledger"><table><thead><tr><th style="width:44px">#</th>
    <th style="width:92px">事件</th><th>內容</th></tr></thead>
    <tbody id="rows"></tbody></table></div>
  <div id="details"><div id="dresize"></div>
    <div id="dtabs"><button id="tC" data-on="true">Content</button><button id="tT">Timing</button></div>
    <div id="dbody"><div class="dempty">點 Overview 色塊或左側列查看詳情</div></div>
  </div>
</div>
<div id="tip"></div>
<script>
const D=__DATA__;const R=D.records;
const t0=Math.min(...R.map(r=>r.start)),t1=Math.max(...R.map(r=>r.end));
const turns=[...new Set(R.map(r=>r.attempt))].sort((a,b)=>a-b);
const turnStart={};R.forEach(r=>{if(!(r.attempt in turnStart)||r.start<turnStart[r.attempt])turnStart[r.attempt]=r.start});
let mode='time';           // time | sequence
let view=null;             // {s,e} zoom viewport(domain 座標);null=全域
let range=null;            // 拖選區間(domain 座標)
let cur=null,hov=null,query='';
const $=id=>document.getElementById(id);
const track=$('track'),rows=$('rows'),tip=$('tip');
const fmtT=t=>new Date(t*1000).toLocaleTimeString('en-GB')+'.'+String(Math.round(t%1*1000)).padStart(3,'0');
const fmtD=s=>s>=1?s.toFixed(2)+' s':Math.round(s*1000)+' ms';
// domain 投影:time=真實秒;sequence=事件序號等寬
const dom=r=>mode==='time'?{s:r.start,e:r.end}:{s:r.i,e:r.i+1};
const D0=()=>mode==='time'?t0:0, D1=()=>mode==='time'?t1:R.length;
const vw=()=>view||{s:D0(),e:D1()};
const frac=x=>{const v=vw();return (x-v.s)/Math.max(1e-9,v.e-v.s)};
function matches(r){return !query||r.text.toLowerCase().includes(query)}
function inRange(r){if(!range)return true;const d=dom(r);return d.e>=range.s&&d.s<=range.e}
function renderOv(){
  track.querySelectorAll('.span,.turnline,.turntag').forEach(n=>n.remove());
  const v=vw(),W=track.clientWidth;
  turns.forEach(a=>{const x=mode==='time'?turnStart[a]:R.find(r=>r.attempt===a).i;
    const f=frac(x);if(f<0||f>1)return;
    const l=document.createElement('div');l.className='turnline';l.style.left=(f*100)+'%';track.appendChild(l);
    const g=document.createElement('div');g.className='turntag';g.style.left=`calc(${f*100}% + 3px)`;g.textContent='a'+a;track.appendChild(g);});
  R.forEach(r=>{const d=dom(r),fs=frac(d.s),fe=frac(d.e);
    if(fe<0||fs>1)return;
    const el=document.createElement('div');el.className='span';
    el.dataset.cat=r.cat;el.style.setProperty('--lane',r.lane);
    el.style.left=Math.max(0,fs*100)+'%';
    el.style.width=Math.max(2,(Math.min(1,fe)-Math.max(0,fs))*W-1)+'px';
    if(range&&!inRange(r))el.classList.add('dim');
    if(!matches(r))el.classList.add('searchdim');
    if(cur===r.i)el.classList.add('cur');if(hov===r.i)el.classList.add('hov');
    el.onmouseenter=ev=>{hov=r.i;el.classList.add('hov');showTip(ev,r)};
    el.onmouseleave=()=>{hov=null;el.classList.remove('hov');hideTip()};
    track.appendChild(el);});
  const sel=$('sel');
  if(range){const fs=Math.max(0,frac(range.s)),fe=Math.min(1,frac(range.e));
    sel.style.display='block';sel.style.left=(fs*100)+'%';sel.style.width=Math.max(1,(fe-fs)*track.clientWidth)+'px';}
  else sel.style.display='none';
}
let tipTimer=null;
function showTip(ev,r){clearTimeout(tipTimer);
  tipTimer=setTimeout(()=>{tip.style.display='block';
    tip.innerHTML='<b>'+r.cat+'</b> a'+r.attempt+' · '+fmtT(r.start)+' · '+fmtD(r.end-r.start)
      +'<br>'+esc(r.text.slice(0,140));
    tip.style.left=Math.min(ev.clientX+12,innerWidth-330)+'px';
    tip.style.top=(ev.clientY+14)+'px';},500);}
function hideTip(){clearTimeout(tipTimer);tip.style.display='none'}
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
function renderLedger(){
  rows.innerHTML='';let lastTurn=null;
  R.forEach(r=>{
    if(range&&!inRange(r))return;              // 拖選聯動:只顯示區間內
    if(r.attempt!==lastTurn){lastTurn=r.attempt;
      const tr=document.createElement('tr');tr.className='turnhead';
      tr.innerHTML='<td colspan="3">— attempt '+r.attempt+' —</td>';rows.appendChild(tr);}
    const tr=document.createElement('tr');tr.className='row';tr.id='r'+r.i;
    if(!matches(r))tr.classList.add('searchdim');
    if(cur===r.i)tr.classList.add('cur');
    tr.innerHTML='<td class="idx">'+r.i+'</td>'
      +'<td><span class="chip" data-cat="'+r.cat+'">'+r.cat+'</span></td>'
      +'<td><span class="prev">'+esc(r.text.slice(0,160))+'</span></td>';
    tr.onclick=()=>select(r.i,false);rows.appendChild(tr);});
}
let dtab='C';
function renderDetails(){
  const b=$('dbody');
  if(cur===null){b.innerHTML='<div class="dempty">點 Overview 色塊或左側列查看詳情</div>';return}
  const r=R[cur];
  if(dtab==='C')b.innerHTML='<pre>'+esc(r.text||'(空)')+'</pre>';
  else b.innerHTML='<dl><dt>category</dt><dd>'+r.cat+'</dd>'
    +'<dt>attempt</dt><dd>a'+r.attempt+'</dd>'
    +'<dt>start</dt><dd>'+fmtT(r.start)+'</dd>'
    +'<dt>duration</dt><dd>'+fmtD(r.end-r.start)+' <span class="idx">(到下一事件;末事件為最小寬)</span></dd>'
    +'<dt>lane</dt><dd>'+['user','agent','tool'][r.lane]+'</dd></dl>';
}
function select(i,scroll){cur=i;renderOv();renderLedger();renderDetails();
  if(scroll){const el=$('r'+i);el&&el.scrollIntoView({block:'center'})}}
function renderAll(){renderOv();renderLedger();renderDetails()}
// ── 互動:wheel 錨點縮放 / 左鍵拖選 / 右鍵平移或清除 ──
track.addEventListener('wheel',ev=>{ev.preventDefault();
  const v=vw(),W=Math.max(1,track.clientWidth);
  const a=(ev.clientX-track.getBoundingClientRect().left)/W;
  const dur=v.e-v.s,full=D1()-D0();
  let nd=Math.min(full,Math.max(full*0.01,dur*Math.exp(ev.deltaY*0.0015)));
  if(nd>=full*0.999){view=null;renderOv();return}
  const anchor=v.s+a*dur;
  let ns=Math.min(Math.max(anchor-a*nd,D0()),D1()-nd);
  view={s:ns,e:ns+nd};renderOv();},{passive:false});
let drag=null;
track.addEventListener('pointerdown',ev=>{
  const v=vw(),x=v.s+((ev.clientX-track.getBoundingClientRect().left)/Math.max(1,track.clientWidth))*(v.e-v.s);
  if(ev.button===2){if(range){range=null;renderAll()}else if(view)drag={pan:true,x0:ev.clientX,v0:{...view}};return}
  drag={x0:x,x1:x,ly:ev.clientY-track.getBoundingClientRect().top,
        hadRange:!!range};
  track.setPointerCapture(ev.pointerId);});
track.addEventListener('pointermove',ev=>{
  const rect=track.getBoundingClientRect(),W=Math.max(1,track.clientWidth);
  const v=vw(),x=v.s+((ev.clientX-rect.left)/W)*(v.e-v.s);
  if(drag&&drag.pan){const d=(drag.x0-ev.clientX)/W*(drag.v0.e-drag.v0.s);
    let ns=Math.min(Math.max(drag.v0.s+d,D0()),D1()-(drag.v0.e-drag.v0.s));
    view={s:ns,e:ns+(drag.v0.e-drag.v0.s)};track.classList.add('pan');renderOv();return}
  if(drag){drag.x1=x;
    if(Math.abs(frac(drag.x1)-frac(drag.x0))>0.005){   // 過閾值才算拖選
      range={s:Math.min(drag.x0,drag.x1),e:Math.max(drag.x0,drag.x1)};renderOv()}
    return}
  const h=$('hline');h.style.display='block';
  h.style.left=`calc(${((ev.clientX-rect.left)/W)*100}% - 1px)`;});
function jumpTo(x){          // 無選取時點擊時間帶:跳到該時刻最近的事件
  let best=null,bd=Infinity;
  R.forEach(r=>{const d=dom(r);
    const dist=(x>=d.s&&x<=d.e)?0:Math.min(Math.abs(d.s-x),Math.abs(d.e-x));
    if(dist<bd){bd=dist;best=r.i}});
  if(best!==null)select(best,true);}   // select=高亮+ledger 捲動+右側 details
function hitSpan(x,ly){      // 點中某泳道的 span?(pointer capture 下 target
  const lane=Math.round((ly-13)/14);   //  永遠是 track,改用座標命中測試)
  let best=null;
  R.forEach(r=>{if(r.lane!==lane)return;const d=dom(r);
    if(x>=d.s&&x<=d.e)best=r.i;});
  return best;}
track.addEventListener('pointerup',ev=>{
  if(drag&&!drag.pan){
    const clicked=Math.abs(frac(drag.x1)-frac(drag.x0))<=0.005;
    if(!clicked)renderAll();                       // 拖選成立→聯動
    else if(drag.hadRange){range=null;renderAll()}  // 原有選取→點擊=清除
    else{range=null;                                // 點擊:點中色塊=選它;
      const hit=hitSpan(drag.x0,drag.ly);           // 空白=跳到該時間
      hit!==null?select(hit,true):jumpTo(drag.x0)}
  }
  track.classList.remove('pan');drag=null;});
track.addEventListener('pointerleave',()=>{$('hline').style.display='none'});
track.addEventListener('contextmenu',ev=>ev.preventDefault());
// 搜尋/投影/頁籤/拖寬
$('q').addEventListener('input',ev=>{query=ev.target.value.trim().toLowerCase();renderAll()});
$('mTime').onclick=()=>{mode='time';view=null;range=null;$('mTime').dataset.on=true;$('mSeq').dataset.on=false;renderAll()};
$('mSeq').onclick=()=>{mode='sequence';view=null;range=null;$('mSeq').dataset.on=true;$('mTime').dataset.on=false;renderAll()};
$('tC').onclick=()=>{dtab='C';$('tC').dataset.on=true;$('tT').dataset.on=false;renderDetails()};
$('tT').onclick=()=>{dtab='T';$('tT').dataset.on=true;$('tC').dataset.on=false;renderDetails()};
(()=>{const d=$('details'),h=$('dresize');let rs=null;
h.addEventListener('pointerdown',ev=>{rs={x0:ev.clientX,w0:d.getBoundingClientRect().width};h.setPointerCapture(ev.pointerId)});
h.addEventListener('pointermove',ev=>{if(!rs)return;d.style.width=Math.max(260,Math.min(innerWidth*.6,rs.w0+(rs.x0-ev.clientX)))+'px'});
h.addEventListener('pointerup',()=>{rs=null});})();
addEventListener('resize',()=>renderOv());
renderAll();
</script></body></html>
"""
