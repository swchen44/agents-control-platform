"""交付物貼回 Jira(結構化 ADF comment + 附件)。設計:docs/design/agent-output.md。

終態時把 agent 產出(structured summary + OUTPUT.json)組成人可讀的 ADF comment:
  - 標題:outcome + attempt + 累計花費
  - 自報:structured.summary(100–200 字 完成/未完成)
  - 程式碼:code[](Gerrit 連結)
  - 附件:<6MB 附到 issue、列檔名;≥6MB 給下載頁連結(Phase 3 form_server /files/<token>)
  - references:只列指標
  - 尾:完整敘事 / 評分表單頁連結(ScoreGate 稍後發)
降級不擲例外(加值性)。build_comment_adf 為純函式(好測);post_deliverables 有副作用。
"""

from __future__ import annotations

from . import adf
from .logutil import get_logger
from .output import Output, attach_mode, load_output, resolve_attachments

log = get_logger("deliverables")


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f}MB"


def build_comment_adf(*, outcome: str, attempt: int, cost_usd: float,
                      self_summary: str, output: Output | None,
                      attach_names: list[str], mode: str,
                      download_url: str | None, base_url: str | None,
                      key: str) -> dict:
    """組交付物 ADF comment(純函式)。mode: none|attach|link(見 output.attach_mode)。"""
    blocks = [adf.heading(
        f"[agent] outcome={outcome}(attempt {attempt},累計 ${cost_usd:.4f})", 3)]
    if self_summary.strip():
        blocks.append(adf.paragraph(adf.strong("自報:"), " " + self_summary.strip()))

    if output and output.code:
        blocks.append(adf.heading("程式碼(Gerrit)", 4))
        items = []
        for c in output.code:
            url = c.get("url") or ""
            label = c.get("note") or c.get("ref") or url or "(change)"
            items.append([adf.link(label, url)] if url else [adf.text(label)])
        blocks.append(adf.bullet_list(items))

    if mode == "attach" and attach_names:
        blocks.append(adf.heading("附件(已附到本票)", 4))
        blocks.append(adf.bullet_list(attach_names))
    elif mode == "link" and attach_names:
        blocks.append(adf.heading("附件(檔案較大,用下載連結)", 4))
        blocks.append(adf.bullet_list(attach_names))
        if download_url:
            blocks.append(adf.paragraph(
                adf.link("⬇ 下載附件(一次性連結)", download_url)))

    if output and output.references:
        blocks.append(adf.heading("參考(指標,未上傳)", 4))
        ritems = []
        for r in output.references:
            tgt = r.get("path_or_url") or ""
            lab = r.get("label") or tgt
            note = f"（{r['note']}）" if r.get("note") else ""
            if tgt.startswith("http"):
                ritems.append([adf.link(lab, tgt), adf.text(note)])
            else:
                ritems.append(adf.text(f"{lab}: {tgt}{note}"))
        blocks.append(adf.bullet_list(ritems))

    if not (output and (output.summary_md or output.code or output.references)) \
            and mode == "none":
        blocks.append(adf.paragraph(
            "(agent 未產出 OUTPUT.json;僅上方自報可參考)"))

    blocks.append(adf.paragraph(
        "完整敘事與評分:稍後會發一次性表單連結給你(HIL(End))。"))
    return adf.doc(*blocks)


def post_deliverables(source, store, ticket, sess, *, outcome: str,
                      self_summary: str, download_url: str | None = None,
                      base_url: str | None = None) -> list[dict]:
    """讀 OUTPUT.json → 附小檔 / 備大檔連結 → 貼 ADF comment → journal。回事件清單。
    全程 best-effort:任何一步失敗都 log + 降級,不擲例外(不擋派工/評分)。"""
    evs: list[dict] = []
    atts, total, skipped, mode = [], 0, [], "none"
    output = load_output(sess.workspace)     # 缺/壞/哨值 ws → None(降級)
    if output is not None:
        atts, total, skipped = resolve_attachments(sess.workspace, output)
        mode = attach_mode(total, len(atts))

    # <6MB → 逐一附到 issue(best-effort;失敗只 log)
    uploaded: list[str] = []
    if mode == "attach":
        import os
        for a in atts:
            try:
                source.add_attachment(ticket.id,
                                      os.path.join(sess.workspace, a.rel))
                uploaded.append(a.name)
            except Exception as e:  # noqa: BLE001
                log.warning("附件上傳失敗 %s %s: %s", ticket.key, a.name, e)
    names = uploaded if mode == "attach" else [a.name for a in atts]

    # 只有真的有交付物(OUTPUT.json 存在)才多貼一則 ADF comment;否則既有 outcome
    # comment 的自報已足夠,不製造噪音(降級只 journal has_output=false)。
    if output is not None:
        try:
            body = build_comment_adf(
                outcome=outcome, attempt=sess.attempts, cost_usd=sess.cost_usd,
                self_summary=self_summary, output=output, attach_names=names,
                mode=mode, download_url=download_url, base_url=base_url,
                key=ticket.key)
            source.add_comment_adf(ticket.id, body,
                                   detail=f"deliverables:{outcome}")
        except Exception as e:  # noqa: BLE001
            log.warning("貼交付物 comment 失敗 %s: %s", ticket.key, e)

    evs.append(store.journal(
        "deliverables_posted", ticket.id, ticket.key,
        has_output=output is not None, n_attachments=len(names),
        mode=mode, skipped=len(skipped)))
    if skipped:
        log.warning("%s 跳過 %d 個附件(不存在/越界): %s",
                    ticket.key, len(skipped), skipped)
    return evs
