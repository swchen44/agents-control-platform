"""內部觸發源(job)——不靠 Jira 票、由排程驅動(J1 統一,2026-08-11)。

每個 job 有 `trigger_type`,兩者都跑 `config/scripts/<subfolder>/…`(cwd 進 subfolder、
log 存 transcript,共用 `_run_logged_script`):
- **script-job**:純做事,stdout 只是 log,不開票。
- **agent-job**:stdout 應為 JSON 任務清單 → 每筆**像人一樣** `create_ticket`
  (description 最上面寫 yaml meta 含 crid;**不建 session、不鎖定 profile**)→ 票走
  poller 既有 route/triage 流程。stdout 非 JSON → `trigger_error`。

排程:`every: 24h`(間隔)或 `cron: "0 3 * * *"`(五欄位牆鐘;`*`/`*/N`/`N-M`/逗號;
dom/dow 都受限時 OR;停機補跑回溯 2 天;cron 與 every 並存 cron 優先)。`count` 次數上限。
冪等:**先記 last_run 再跑**(at-most-once)。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

import yaml

from .logutil import get_logger
from .paths import resolve_config_script as _resolve_script
from .routing import ConfigError
from .store import TicketSession

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
    """J1(2026-08-11):統一 job。trigger_type 決定行為,兩者都跑 `script`
    (config/scripts/<subfolder>/…;執行 cwd 進 subfolder;log 存 transcript):
    - script-job:純做事,stdout 只是 log。
    - agent-job:stdout 應為 JSON 任務清單 → 每筆像人一樣 create_ticket(不建
      session、不鎖定 profile)→ 走 poller route/triage。"""
    name: str
    run_name: str
    trigger_type: str                 # "agent-job" | "script-job"
    script: list[str]                 # 相對 config/scripts/ 的 argv(argv[0]=腳本檔)
    every_sec: float | None = None    # None = 只能 oneshot(CLI)
    timeout_sec: float = 600.0
    cron: str | None = None           # W4.6:原始 cron 字串(顯示/journal 用)
    cron_spec: dict | None = None     # 解析後(parse_cron);優先於 every
    count: int = 1                    # 次數上限(0=無上限,需 cron;1=單次;N=N 次)
    labels: list[str] = field(default_factory=list)  # agent-job 開票帶的 labels


def load_triggers(path: str, profiles: dict | None = None) -> list[Trigger]:
    """fail-fast 載入(壞 config 死在 load,不是觸發時)。J1:每個 trigger 必填
    trigger_type(agent-job/script-job)+ script;agent-job 建議設 labels(開票路由用)。"""
    import shlex
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
        ttype = str(t.get("trigger_type") or "")
        if ttype not in ("agent-job", "script-job"):
            raise ConfigError(f"trigger {name}: trigger_type 必填為 "
                              f"agent-job 或 script-job(拿到 {ttype!r})")
        script = t.get("script")
        if isinstance(script, str):
            script = shlex.split(script)
        if not (isinstance(script, list) and script
                and all(isinstance(x, str) for x in script)):
            raise ConfigError(f"trigger {name}: script 必填(字串或字串列表;"
                              f"argv[0]=config/scripts/ 下的腳本路徑)")
        labels = list(t.get("labels") or [])
        if ttype == "agent-job" and not labels:
            raise ConfigError(f"trigger {name}: agent-job 需 labels"
                              f"(開的票靠它命中 route)")
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
            log.warning("trigger %s: cron 與 every 並存 → cron 優先"
                        "(every=%s 忽略)", name, every)
            every_sec = None
        count = int(t.get("count", 1))
        if count < 0:
            raise ConfigError(f"trigger {name}: count 不可為負(拿到 {count})")
        if count != 1 and cron_spec is None and every_sec is None:
            raise ConfigError(f"trigger {name}: count={count}(循環/多次)需要 "
                              f"cron 或 every 排程")
        out.append(Trigger(name=name, run_name=run_name, trigger_type=ttype,
                           script=script, every_sec=every_sec,
                           timeout_sec=float(t.get("timeout_sec", 600)),
                           cron=str(cron) if cron is not None else None,
                           cron_spec=cron_spec, count=count, labels=labels))
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


# M1:_resolve_script 提升為 paths.resolve_config_script(相對 config/scripts/、
# 強制 subfolder、cwd 切 subfolder、擋越界),與 selection(select.script)共用。


_META_KEYS = ("crid", "prompt", "email")


def _ticket_meta_yaml(meta) -> str:
    """已知欄位 → description 最上面的 yaml 區塊(J2;人可讀,dispatcher 讀回)。"""
    lines = [f"{k}: {meta[k]}" for k in _META_KEYS
             if meta.get(k) not in (None, "")]
    return ("\n".join(lines) + "\n\n") if lines else ""


def parse_ticket_meta(description) -> dict:
    """讀 description 最上面的 yaml 契約區塊(J2:key: value,只認已知 key,到空行止;
    ARCP sections 之外,人寫或 agent-job 寫)。回 {crid?, prompt?, email?}。"""
    from .sections import parse
    before, _secs, after = parse(description or "")
    text = before if before.strip() else after   # 佈建前在 before、後在 after
    out: dict = {}
    for line in text.splitlines():
        if not line.strip():
            break                                 # 空行 → meta 區塊結束
        k, sep, v = line.partition(":")
        if sep and k.strip() in _META_KEYS:
            out[k.strip()] = v.strip()
    return out


def _run_logged_script(trigger: "Trigger", store, root: str,
                       now: float | None = None):
    """跑 trigger.script(cwd 進 config/scripts/<subfolder>)並存 log:
    runs/{name}__{run_name}__{ts}/ 下 ws/ + transcript/(stdout/stderr.log + run.tgz)。
    註冊 TicketSession(profile=<type>:<name>)→ dashboard 可見可下載。回
    (rc, stdout 文字, events)。rc==0→SUCCESS,否則 FAILURE。兩種 trigger_type 共用。"""
    import subprocess
    import tarfile
    now = time.time() if now is None else now
    ts = int(now)
    store.set_trigger_last_run(trigger.name, now)     # 先記水位(at-most-once)
    base = f"{root}/runs/{trigger.name}__{trigger.run_name}__{ts}"
    ws, tdir = f"{base}/ws", f"{base}/transcript"
    os.makedirs(ws, exist_ok=True)
    os.makedirs(tdir, exist_ok=True)
    try:
        abs_argv, cwd = _resolve_script(trigger.script)
    except (ConfigError, RuntimeError, ValueError) as e:
        return None, "", [store.journal("trigger_error", ts, trigger.run_name,
                                        error=str(e)[:200])]
    events = [store.journal("script_run_started", ts, trigger.run_name,
                            trigger=trigger.name,
                            script=" ".join(trigger.script), cwd=cwd)]
    log.info("%s %s 啟動:%s(cwd=%s)", trigger.trigger_type, trigger.name,
             " ".join(trigger.script), cwd)
    rc, timed_out = None, False
    t0 = time.time()
    with open(f"{tdir}/stdout.log", "wb") as so, \
            open(f"{tdir}/stderr.log", "wb") as se:
        try:
            rc = subprocess.run(abs_argv, cwd=cwd, stdout=so, stderr=se,
                                stdin=subprocess.DEVNULL,
                                timeout=trigger.timeout_sec).returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        except OSError as e:
            se.write(f"[arcp] 無法執行:{e}".encode())
    dur = time.time() - t0
    with tarfile.open(f"{tdir}/run.tgz", "w:gz", compresslevel=9) as tf:
        for n in ("stdout.log", "stderr.log"):
            tf.add(f"{tdir}/{n}", arcname=n)
    try:
        stdout_text = open(f"{tdir}/stdout.log", encoding="utf-8",
                           errors="replace").read()
    except OSError:
        stdout_text = ""
    outcome = "SUCCESS" if rc == 0 else "FAILURE"
    store.upsert_session(TicketSession(
        issue_id=ts, key=trigger.run_name,
        profile=f"{trigger.trigger_type}:{trigger.name}", workspace=ws,
        session_id=None, attempts=1, outcome=outcome, pending_reason=None,
        cost_usd=0.0))
    events.append(store.journal(
        "script_run_finished", ts, trigger.run_name, trigger=trigger.name,
        rc=rc, timeout=timed_out, duration_sec=round(dur, 1), outcome=outcome))
    log.info("%s %s %s(rc=%s%s,%.1fs)", trigger.trigger_type, trigger.name,
             outcome, rc, ",timeout" if timed_out else "", dur)
    return rc, stdout_text, events


def run_script_trigger(trigger: "Trigger", store, root: str,
                       now: float | None = None) -> list[dict]:
    """script-job:純跑 script(logged)、不開票。回 events。"""
    _rc, _out, events = _run_logged_script(trigger, store, root, now)
    return events


def fire_agent_job(trigger: "Trigger", source, store, root: str,
                   project: str, now: float | None = None) -> list[dict]:
    """agent-job:跑 script(logged)→ stdout JSON 任務清單 → 每筆**像人一樣**
    create_ticket(description 最上面寫 yaml meta 含 crid;不建 session、不鎖定 profile)
    → 票走 poller route/triage。stdout 非 JSON(應為任務)→ trigger_error。回 events。"""
    import json as _json
    rc, out_text, events = _run_logged_script(trigger, store, root, now)
    if rc != 0:
        events.append(store.journal("trigger_error", 0, trigger.run_name,
                                    error=f"agent-job script rc={rc}"))
        return events
    try:
        data = _json.loads(out_text or "[]")
    except ValueError:
        events.append(store.journal(
            "trigger_error", 0, trigger.run_name,
            error="agent-job stdout 非 JSON(應為任務清單;看 transcript/stderr.log)"))
        return events
    items = data if isinstance(data, list) else [data]
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        desc = str(it.get("description") or it.get("summary") or "").strip()
        if not desc:
            continue
        summary = str(it.get("summary") or f"job:{trigger.run_name}")[:200]
        labels = list(it.get("labels") or trigger.labels)
        crid = it.get("crid")
        full_desc = _ticket_meta_yaml({"crid": crid}) + desc
        try:
            t = source.create_ticket(project, summary, full_desc, labels=labels)
        except Exception as e:  # noqa: BLE001 — 單筆失敗不擋其餘
            log.warning("job %s create_ticket 失敗:%s", trigger.name, e)
            events.append(store.journal("trigger_error", 0, trigger.run_name,
                                        error=str(e)[:200]))
            continue
        events.append(store.journal("job_fired", t.id, t.key, job=trigger.name,
                                    run_name=trigger.run_name, task_idx=idx,
                                    crid=crid))
        log.info("job %s → 開票 %s(不鎖定 profile、走 route)", trigger.name, t.key)
    return events
