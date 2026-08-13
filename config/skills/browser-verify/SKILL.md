---
name: browser-verify
description: 用 agent-browser CLI(Rust/CDP,免 Playwright)對 web 頁面做驗收:開頁、快照、互動、截圖、產 REPORT.md。適用「照 checklist 驗 dashboard/表單頁」類任務。
---

# browser-verify — 用 agent-browser 驗收 web 頁面

你有 `Bash(agent-browser:*)` 權限。agent-browser 是 CLI 瀏覽器
(Chrome via CDP);**不要**嘗試 playwright/puppeteer/curl 解析 HTML。

## 核心迴圈

```bash
agent-browser open <url>          # 開頁(瀏覽器跨命令保持存活)
agent-browser snapshot -i         # accessibility 快照(只列互動元素,省 token)
agent-browser click @e3           # 用快照給的 @eN ref 互動
agent-browser fill @e4 "文字"     # 填欄位
agent-browser screenshot 名.png   # 截圖(存到目前工作目錄)
agent-browser close               # 全部做完才關
```

- `@eN` ref **每次快照重新編號**;頁面一變(點擊/導航/提交)必須重新
  `snapshot -i` 再用新 ref。
- 讀文字內容:`agent-browser snapshot`(全樹)或 `snapshot -s "#css"` 縮範圍。
- 等待載入:`agent-browser wait --load networkidle`。

## 驗收約定(ARCP)

1. TICKET.md 的 checklist **逐項驗**:每項「打開哪頁 → 看什麼 → 判定 PASS/FAIL」。
2. 每項至少一張截圖存 workspace(檔名=項目編號,如 `B1.png`)。
3. 產出 **`REPORT.md`**:每項一節——`## <項目> — PASS|FAIL`+一句判定依據
   +截圖檔名;最後總結行 `RESULT: <n> PASS / <m> FAIL`。
4. **FAIL 也要如實記**(你的工作是驗收,不是讓報告好看);打不開的頁記
   FAIL+錯誤訊息。
5. 把 REPORT.md 與截圖列入 OUTPUT.json 的 `attachments`。
