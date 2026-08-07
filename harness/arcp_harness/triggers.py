"""W3.4 — scheduled/oneshot 內部觸發源(DESIGN §5 / W20)。

profile 不只被 Jira 票驅動,也能被內部觸發器啟動,走同一條
provision→fork→grade 管線,差別只在:
- **無票面**:pseudo-Ticket(id=timestamp、key=run_name)→ 命名自然成為
  DESIGN §2 的無票格式 `{agent}__{run_name}__{timestamp}`;prompt 來自
  trigger config,渲染進 TICKET.md(agent 讀法不變)。
- **不經審批門**(W20):config 寫了 trigger 即視為授權。
- **結果進 journal/dashboard**,不寫 Jira comment。

觸發方式:
- scheduled 間隔:`every: 24h`(級距 Nm/Nh/Nd,「距上次 ≥N 就 due」)。
- scheduled 牆鐘:`cron: "0 3 * * *"`(W4.6,五欄位 crontab:分 時 日 月 週;
  支援 `*`、`*/N`、`N-M`、逗號列表;dom/dow 都受限時取 OR——vixie cron 慣例;
  分鐘粒度;停機期間錯過的點**補跑一次**,回溯上限 2 天;首次啟動不回溯,
  只看當下分鐘)。**cron 與 every 同時給時 cron 優先 + warning**。
- oneshot:`python3 run_trigger.py <名>`(CLI,忽略排程/last_run)。
冪等取向:**先記 last_run 再跑**(at-most-once——crash 寧可少跑一輪,
下次 due 再補,不重複跑)。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

import yaml

from .dispatcher import BASE_PROMPT, _grader
from .inner_runner import run_attempt
from .logutil import get_logger
from .profiles import Profile
from .routing import ConfigError
from .store import TicketSession
from .ticket import Ticket
from .workspace import provision

log = get_logger("triggers")

_RUN_NAME_RE = re.compile(r"^[a-z0-9-]+$")     # 防 path 注入(DESIGN §10)
_EVERY_RE = re.compile(r"^(\d+)([mhd])$")
_UNIT_SEC = {"m": 60, "h": 3600, "d": 86400}

# -- W4.6 crontab(五欄位)------------------------------------------------- #
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))  # 分 時 日 月 週
_CRON_LOOKBACK_MIN = 2880              # 停機補跑回溯上限(2 天)


def _parse_cron_field(text: str, lo: int, hi: int) -> set[int]:
    vals: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step <= 0:
                raise ValueError(f"step 必須 >0: {s}")
        if part == "*":
            rng = range(lo, hi + 1)
        elif "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
            if not (lo <= a <= b <= hi):
                raise ValueError(f"範圍越界: {part}")
            rng = range(a, b + 1)
        else:
            v = int(part)
            if not (lo <= v <= hi):
                raise ValueError(f"值越界: {part}")
            rng = range(v, v + 1)
        vals.update(rng[::step])
    if not vals:
        raise ValueError("空欄位")
    return vals


def parse_cron(spec: str) -> dict:
    """五欄位 crontab → {min,hour,dom,mon,dow, dom_star,dow_star}。壞格式擲
    ConfigError(load 時 fail-fast)。"""
    fields = spec.split()
    if len(fields) != 5:
        raise ConfigError(f"cron 需五欄位(分 時 日 月 週),拿到 {spec!r}")
    try:
        parsed = [_parse_cron_field(f, lo, hi)
                  for f, (lo, hi) in zip(fields, _CRON_RANGES)]
    except ValueError as e:
        raise ConfigError(f"cron 欄位不合法({spec!r}):{e}") from e
    dow = parsed[4]
    if 7 in dow:                       # 週日 7 與 0 等價
        dow.add(0)
    return {"min": parsed[0], "hour": parsed[1], "dom": parsed[2],
            "mon": parsed[3], "dow": dow,
            "dom_star": fields[2] == "*", "dow_star": fields[4] == "*"}


def _cron_matches(c: dict, dt) -> bool:
    if (dt.minute not in c["min"] or dt.hour not in c["hour"]
            or dt.month not in c["mon"]):
        return False
    dom_ok = dt.day in c["dom"]
    dow_ok = (dt.weekday() + 1) % 7 in c["dow"]   # python Mon=0 → cron Sun=0
    if not c["dom_star"] and not c["dow_star"]:
        return dom_ok or dow_ok        # vixie cron:兩者都受限 → OR
    return dom_ok and dow_ok


def _cron_due(c: dict, last_run: float, now: float) -> bool:
    """(last_run, now] 間是否存在符合的分鐘點。分鐘粒度;首跑(last_run=0)
    不回溯只看當下分鐘;停機錯過的點補跑一次(回溯上限 2 天)。"""
    import datetime
    now_min = int(now // 60)
    start = int(last_run // 60) + 1 if last_run > 0 else now_min
    start = max(start, now_min - _CRON_LOOKBACK_MIN + 1)
    for m in range(start, now_min + 1):
        if _cron_matches(c, datetime.datetime.fromtimestamp(m * 60)):
            return True
    return False


@dataclass
class Trigger:
    name: str
    profile: str | None          # 與 script 互斥
    run_name: str
    prompt: str
    every_sec: float | None      # None = 只能 oneshot(CLI)
    script: list[str] | None = None   # W4.4:任意執行檔 argv(uvx/npx/.sh/.py…)
    timeout_sec: float = 600.0        # script 用
    cron: str | None = None           # W4.6:原始 cron 字串(顯示/journal 用)
    cron_spec: dict | None = None     # 解析後(parse_cron);優先於 every


def load_triggers(path: str, profiles: dict[str, Profile]) -> list[Trigger]:
    """fail-fast 載入(壞 config 死在 load,不是觸發時)。"""
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    out: list[Trigger] = []
    for i, t in enumerate(((doc.get("outer_loop") or {}).get("triggers")
                           or [])):
        name = t.get("name") or f"trigger[{i}]"
        run_name = str(t.get("run_name") or "")
        if not _RUN_NAME_RE.match(run_name):
            raise ConfigError(f"trigger {name}: run_name 必填且限 [a-z0-9-]"
                              f"(拿到 {run_name!r})")
        prof = t.get("profile")
        script = t.get("script")
        if (prof is None) == (script is None):        # W4.4:恰好其一
            raise ConfigError(f"trigger {name}: profile 與 script 擇一必填")
        if script is not None:                        # 萬用 script(argv)
            if isinstance(script, str):
                import shlex
                script = shlex.split(script)
            if not (isinstance(script, list) and script
                    and all(isinstance(x, str) for x in script)):
                raise ConfigError(f"trigger {name}: script 需字串或字串列表")
        else:
            if prof not in profiles:
                raise ConfigError(f"trigger {name}: profile 不存在: {prof!r}")
            if not str(t.get("prompt") or "").strip():
                raise ConfigError(f"trigger {name}: prompt 必填")
        every = t.get("every")
        every_sec = None
        if every is not None:
            m = _EVERY_RE.match(str(every))
            if not m:
                raise ConfigError(f"trigger {name}: every 格式 N[mhd]"
                                  f"(拿到 {every!r})")
            every_sec = int(m.group(1)) * _UNIT_SEC[m.group(2)]
        cron = t.get("cron")
        cron_spec = parse_cron(str(cron)) if cron is not None else None
        if cron_spec is not None and every_sec is not None:
            # 使用者定案(2026-08-06):並存時 cron 優先 + warning
            log.warning("trigger %s: cron 與 every 並存 → cron 優先"
                        "(every=%s 忽略)", name, every)
            every_sec = None
        out.append(Trigger(name=name, profile=prof, run_name=run_name,
                           prompt=str(t.get("prompt") or ""),
                           every_sec=every_sec, script=script,
                           timeout_sec=float(t.get("timeout_sec", 600)),
                           cron=str(cron) if cron is not None else None,
                           cron_spec=cron_spec))
    return out


def due(trigger: Trigger, store, now: float | None = None) -> bool:
    """scheduled 判定:cron(牆鐘)優先於 every(間隔);兩者皆缺 =
    oneshot,永不自動 due(只走 CLI)。"""
    now = time.time() if now is None else now
    if trigger.cron_spec is not None:              # W4.6 牆鐘排程
        return _cron_due(trigger.cron_spec,
                         store.trigger_last_run(trigger.name), now)
    if trigger.every_sec is None:
        return False
    return now - store.trigger_last_run(trigger.name) >= trigger.every_sec


def run_script_trigger(trigger: Trigger, store, root: str,
                       now: float | None = None) -> list[dict]:
    """W4.4 萬用 script trigger:任意執行檔(uvx/npx/.sh/.py…)argv 直接跑。

    run dir = <root>/runs/{name}__{run_name}__{ts}/:
        ws/                script 的 cwd(產物留原地,retention 照收)
        transcript/        stdout.log / stderr.log / run.tgz(gzip -9)
    結束後註冊 TicketSession(issue_id=ts、profile=script:<name>)→ dashboard
    列表/徽章/transcript 卡(log 檢視+tgz 下載)/retention 全部自動重用。
    rc==0 → SUCCESS;rc!=0 或 timeout → FAILURE(journal 記 rc/timeout)。
    """
    import subprocess
    import tarfile
    now = time.time() if now is None else now
    ts = int(now)
    store.set_trigger_last_run(trigger.name, now)    # 先記水位(at-most-once)
    base = f"{root}/runs/{trigger.name}__{trigger.run_name}__{ts}"
    ws = f"{base}/ws"
    tdir = f"{base}/transcript"
    os.makedirs(ws, exist_ok=True)
    os.makedirs(tdir, exist_ok=True)
    events = [store.journal("script_run_started", ts, trigger.run_name,
                            trigger=trigger.name, script=trigger.script,
                            cwd=ws)]
    log.info("script trigger %s 啟動:%s", trigger.name, trigger.script)
    rc: int | None = None
    timed_out = False
    t0 = time.time()
    with open(f"{tdir}/stdout.log", "wb") as so, \
            open(f"{tdir}/stderr.log", "wb") as se:
        try:
            rc = subprocess.run(trigger.script, cwd=ws, stdout=so, stderr=se,
                                stdin=subprocess.DEVNULL,
                                timeout=trigger.timeout_sec).returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        except OSError as e:                        # 找不到執行檔等
            se.write(f"[arcp] 無法執行:{e}".encode())
    dur = time.time() - t0
    with tarfile.open(f"{tdir}/run.tgz", "w:gz", compresslevel=9) as tf:
        for n in ("stdout.log", "stderr.log"):
            tf.add(f"{tdir}/{n}", arcname=n)
    outcome = "SUCCESS" if rc == 0 else "FAILURE"
    from .store import TicketSession
    store.upsert_session(TicketSession(
        issue_id=ts, key=trigger.run_name, profile=f"script:{trigger.name}",
        workspace=ws, session_id=None, attempts=1, outcome=outcome,
        pending_reason=None, cost_usd=0.0))
    events.append(store.journal(
        "script_run_finished", ts, trigger.run_name, trigger=trigger.name,
        rc=rc, timeout=timed_out, duration_sec=round(dur, 1),
        outcome=outcome))
    log.info("script trigger %s %s(rc=%s%s,%.1fs)", trigger.name, outcome,
             rc, ",timeout" if timed_out else "", dur)
    return events


def run_trigger(trigger: Trigger, profiles: dict[str, Profile], store,
                root: str, now: float | None = None) -> list[dict]:
    """跑一輪 trigger:pseudo-ticket → provision → 證據迴圈 → journal。

    迷你派工(dispatcher 減去 Jira 面):同 grader/三態語意;session 存
    TicketSession(issue_id=timestamp,不與 Jira id 衝突)→ dashboard 可見、
    retention 照收。script 型 trigger(W4.4)委派 run_script_trigger。
    """
    if trigger.script is not None:                  # W4.4 萬用 script
        return run_script_trigger(trigger, store, root, now)
    now = time.time() if now is None else now
    ts = int(now)
    profile = profiles[trigger.profile]
    store.set_trigger_last_run(trigger.name, now)   # 先記水位(at-most-once)
    ticket = Ticket(id=ts, key=trigger.run_name, summary=f"trigger:{trigger.name}",
                    state="internal", assignee=None, assignee_id=None,
                    description=trigger.prompt)
    ws = provision(root, ticket, profile)
    sess = TicketSession(issue_id=ts, key=trigger.run_name,
                         profile=profile.name, workspace=ws, session_id=None,
                         attempts=0, outcome=None, pending_reason=None,
                         cost_usd=0.0)
    store.upsert_session(sess)
    events = [store.journal("trigger_started", ts, trigger.run_name,
                            trigger=trigger.name, profile=profile.name,
                            workspace=ws)]
    log.info("trigger %s 啟動(run=%s ws=%s)", trigger.name, trigger.run_name, ws)

    grader = _grader(profile)
    artifacts = f"{ws.rstrip('/').rsplit('/ws', 1)[0]}/attempts" \
        if ws.endswith("/ws") else ws + ".attempts"
    feedback: str | None = None
    while sess.attempts < profile.max_attempts:
        sess.attempts += 1
        prompt = BASE_PROMPT if not feedback else (
            f"{BASE_PROMPT}\n\n上次嘗試未過驗證,失敗證據:\n{feedback}\n"
            f"請只修正缺失的部分。")
        res = run_attempt(dict(profile.agent), ws, prompt, artifacts,
                          sess.attempts, resume_session_id=sess.session_id)
        sess.session_id = res.session_id or sess.session_id
        sess.cost_usd += res.cost_usd or 0.0
        events.append(store.journal(
            "attempt_finished", ts, trigger.run_name, attempt=sess.attempts,
            raw=res.raw_outcome, error_kind=res.error_kind))
        if res.raw_outcome == "unknown":            # 同 v5 D3:不自動重試
            sess.outcome, sess.pending_reason = "UNKNOWN", "unknown"
            store.upsert_session(sess)
            events.append(store.journal("trigger_finished", ts,
                                        trigger.run_name, outcome="UNKNOWN"))
            return events
        verdict = grader.grade(ws)
        if verdict.passed and res.raw_outcome == "completed":
            sess.outcome = "SUCCESS"
            store.upsert_session(sess)
            events.append(store.journal(       # W3.5 C3 / W7 R3:預設 240 分
                "trigger_finished", ts, trigger.run_name, outcome="SUCCESS",
                attempts=sess.attempts, cost_usd=sess.cost_usd,
                human_minutes_saved=profile.est_minutes()))
            log.info("trigger %s SUCCESS(%d attempt, $%.4f)",
                     trigger.name, sess.attempts, sess.cost_usd)
            return events
        feedback = verdict.summary()
        store.upsert_session(sess)

    sess.outcome, sess.pending_reason = "FAILURE", "max-attempts"
    store.upsert_session(sess)
    events.append(store.journal("trigger_finished", ts, trigger.run_name,
                                outcome="FAILURE", attempts=sess.attempts))
    log.info("trigger %s FAILURE(max-attempts)", trigger.name)
    return events
