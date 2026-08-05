"""W11/W10 — description 分區段協作(DESIGN §4.2 定案版 2026-08-05)。

多方(human / control / agent:<名>)在同一段 description 各有專屬區段:只寫自己的、
對別人 read-only。機器區段(control/agent)附 sha256 短 hash → 防篡改(不符=被誤改→
還原並 log)+ 幂等(hash 沒變就不重寫 description,省 Jira 寫)。

定案版面(2026-08-05):

    <!-- ARCP:sections v1 -->
    ### [ARCP owner=human]
    ```yaml
    agent_name:            # ← 請填(從 reviewer|fixer|… 擇一)
    param:                 # 選填
    ```
    ### [ARCP owner=control updated=<iso>]
    ```yaml
    template: templates/python-fix
    status: awaiting-approval
    ```
    hash: 3f8a1c9e0b2d
    ### [ARCP owner=agent:reviewer updated=<iso>]
    ```yaml
    result: passed
    ```
    hash: a1b2c3d4e5f6
    <!-- /ARCP:sections -->

    <原始需求 + 任何其它資訊 —— 一律不碰>

定案要點:
- 區塊**整個置頂**;內部順序 human → control → agent:<名>(render 自動排,human
  最前方便人填)。
- 開始 `<!-- ARCP:sections v1 -->` + 結束 `<!-- /ARCP:sections -->` 界定;區塊**外**
  (前後)所有非區段內容一律不碰(parse 原樣帶回 before/after,只修剪界線空行)。
- 每次讀 description **掃全部機器段驗 hash**(純 python 不花 token):不符=被誤改 →
  還原 + log(段名;formatter 附時間)+ 由呼叫端貼 comment;human 段永遠尊重。
- owner:control | human | agent:<名>。段內 key snake_case;跨段引用 owner.key;
  附件 `key: attach:<檔名>`(檔名由 comment 附件提供)。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import yaml

from .logutil import get_logger

log = get_logger("sections")

MARKER = "<!-- ARCP:sections v1 -->"
END_MARKER = "<!-- /ARCP:sections -->"
_HEAD_RE = re.compile(
    r"^###\s*\[ARCP\s+owner=([^\s\]]+)(?:\s+updated=([^\s\]]+))?\s*\]\s*$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _order_key(owner: str) -> tuple[int, str]:
    """canonical 版面順序:human 最前 → control → agent:<名>(名字排序)。"""
    if owner == "human":
        return (0, "")
    if owner == "control":
        return (1, "")
    return (2, owner)


def _trim_edges(text: str) -> str:
    r"""去首尾空白行(界線用),保留內部;統一 \n。區塊外內容只修這層界線。"""
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


@dataclass
class Section:
    owner: str                 # control | human | agent:<name>
    body: str                  # YAML 文本(段內容,不含標題/hash 行)
    updated: str = ""
    hash: str | None = None    # 讀入時附帶的 hash(機器段);human 段 None

    def is_machine(self) -> bool:
        return self.owner == "control" or self.owner.startswith("agent:")

    def data(self) -> dict:
        try:
            d = yaml.safe_load(self.body)
        except yaml.YAMLError:
            return {}
        return d if isinstance(d, dict) else {}


def section_hash(body: str) -> str:
    r"""規範化(去首尾空行、每行 rstrip、統一 \n)後 sha256 前 12 hex。"""
    lines = [ln.rstrip() for ln in body.replace("\r\n", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]


def _parse_block(block: str) -> list[Section]:
    """解析 MARKER…END_MARKER 之間的區塊 → sections。"""
    sections: list[Section] = []
    lines = block.split("\n")
    i = 0
    while i < len(lines):
        m = _HEAD_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        owner, updated = m.group(1), (m.group(2) or "")
        i += 1
        body_lines: list[str] = []
        if i < len(lines) and lines[i].strip().startswith("```"):
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body_lines.append(lines[i])
                i += 1
            i += 1  # 跳過收尾 ```
        h = None
        if i < len(lines) and lines[i].strip().startswith("hash:"):
            h = lines[i].strip()[len("hash:"):].strip()
            i += 1
        sections.append(Section(owner=owner, body="\n".join(body_lines),
                                updated=updated, hash=h))
    return sections


def parse(description: str) -> tuple[str, list[Section], str]:
    """→ (before, sections, after)。

    ARCP 區塊由 MARKER … END_MARKER 界定;before/after 為區塊外內容(一律不碰,
    只修剪界線空行)。無 MARKER → (全文, [], "")。有 MARKER 無 END_MARKER →
    區塊延伸到文末(after="")。
    """
    if MARKER not in description:
        return description, [], ""
    before, _, rest = description.partition(MARKER)
    if END_MARKER in rest:
        block, _, after = rest.partition(END_MARKER)
    else:
        block, after = rest, ""
    return _trim_edges(before), _parse_block(block), _trim_edges(after)


def render(before: str, sections: list[Section], after: str = "") -> str:
    """組回 description。ARCP 區塊置頂(sections 依 canonical 序:human→control→
    agent);before/after 原樣保留在區塊前後。機器段自動附最新 hash(幂等的基礎)。"""
    out: list[str] = []
    if before.strip():
        out.append(_trim_edges(before))
        out.append("")            # 界線空行(區塊在其下)
    out.append(MARKER)
    for s in sorted(sections, key=lambda x: _order_key(x.owner)):
        head = f"### [ARCP owner={s.owner}"
        if s.updated:
            head += f" updated={s.updated}"
        out.append(head + "]")
        out.append("```yaml")
        out.append(s.body.strip("\n"))
        out.append("```")
        if s.is_machine():
            out.append(f"hash: {section_hash(s.body)}")
    out.append(END_MARKER)
    if after.strip():
        out.append("")            # 界線空行(原始需求等在其下)
        out.append(_trim_edges(after))
    return "\n".join(out) + "\n"


def validate_keys(section: Section) -> list[str]:
    """回不合命名規範的 key(非 snake_case)。"""
    return [k for k in section.data() if not _KEY_RE.match(str(k))]


def resolve_attachments(section: Section) -> dict[str, str]:
    """`key: attach:<檔名>` → {key: 檔名}。"""
    return {k: v[len("attach:"):].strip()
            for k, v in section.data().items()
            if isinstance(v, str) and v.startswith("attach:")}


def verify_and_restore(sections: list[Section],
                       authoritative: dict[str, Section]
                       ) -> tuple[list[Section], list[str]]:
    """全掃描所有機器段驗 hash(純 python,不花 token)。

    機器段附帶 hash ≠ 重算 hash → 被誤改:有權威版就還原該段,否則保留現況;兩者都
    log(段名;formatter 附時間)並列入 violations 供呼叫端貼 comment。human 段永遠
    尊重。回 (restored_sections, violations[owner 名])。
    """
    restored: list[Section] = []
    violations: list[str] = []
    for s in sections:
        if not s.is_machine() or s.hash is None:
            restored.append(s)                       # human/無 hash:永遠尊重
            continue
        actual = section_hash(s.body)
        if actual == s.hash:
            restored.append(s)                       # hash 符:未動
            continue
        violations.append(s.owner)
        if s.owner in authoritative:
            log.warning("機器段 owner=%s hash 不符(帶 %s / 實算 %s)——被誤改,已還原",
                        s.owner, s.hash, actual)
            restored.append(authoritative[s.owner])
        else:
            log.warning("機器段 owner=%s hash 不符(帶 %s / 實算 %s)——被誤改,"
                        "無權威版可還原,保留現況", s.owner, s.hash, actual)
            restored.append(s)
    return restored, violations
