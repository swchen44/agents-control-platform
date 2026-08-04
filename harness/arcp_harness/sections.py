"""W11/W10 — description 分區段協作(DESIGN §4.2 升級版)。

多方(control / human / agent:<名>)在同一段 description 各有專屬區段:只寫自己的、
對別人 read-only。機器區段(control/agent)附 sha256 短 hash → 防篡改(不符=被誤改→
還原)+ 幂等(hash 沒變就不重寫 description,省 Jira 寫)。

格式:
    <原始需求…頂部不動…>

    <!-- ARCP:sections v1 -->
    ### [ARCP owner=control updated=<iso>]
    ```yaml
    key: value
    ```
    hash: <12hex>          # 機器段才有;human 段無

owner:control | human | agent:<名>。段內 key snake_case(像變數);跨段引用 owner.key;
附件 `key: attach:<檔名>`(檔名由 comment 附件提供)。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import yaml

MARKER = "<!-- ARCP:sections v1 -->"
_HEAD_RE = re.compile(
    r"^###\s*\[ARCP\s+owner=([^\s\]]+)(?:\s+updated=([^\s\]]+))?\s*\]\s*$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


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


def parse(description: str) -> tuple[str, list[Section]]:
    """→ (preamble 原始需求, sections)。無標記 → (全文, [])。"""
    if MARKER not in description:
        return description, []
    pre, _, rest = description.partition(MARKER)
    sections: list[Section] = []
    lines = rest.split("\n")
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
    return pre.rstrip(), sections


def render(preamble: str, sections: list[Section]) -> str:
    """組回 description 文字。機器段自動附最新 hash(幂等的基礎)。"""
    out = [preamble.rstrip(), "", MARKER] if preamble.strip() else [MARKER]
    for s in sections:
        head = f"### [ARCP owner={s.owner}"
        if s.updated:
            head += f" updated={s.updated}"
        out.append(head + "]")
        out.append("```yaml")
        out.append(s.body.strip("\n"))
        out.append("```")
        if s.is_machine():
            out.append(f"hash: {section_hash(s.body)}")
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
    """機器段 hash 不符(被誤改)→ 用 authoritative[owner] 還原;human 段永遠尊重。
    回 (restored_sections, violations[owner 名])。"""
    restored: list[Section] = []
    violations: list[str] = []
    for s in sections:
        if (s.is_machine() and s.hash is not None
                and s.hash != section_hash(s.body)
                and s.owner in authoritative):
            restored.append(authoritative[s.owner])
            violations.append(s.owner)
        else:
            restored.append(s)
    return restored, violations
