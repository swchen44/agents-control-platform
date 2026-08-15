# Agent Loop 可視化三方比較 — DeepSeek Trajectory / claude-code-log / ARCP Timeline

> 2026-08-15。研究 `~/git/deepseek-harness` 的 `packages/client/ui-trajectory/`
> (React,~7,200 行)與我們 vendored 的 claude-code-log(`vendor/cclog/`,
> jinja2+離線 js,~5,900 行模板),對照 ARCP detail_server 的 timeline,
> 萃取可學清單。結論:**排版抄 Trajectory 三件套、工程維持我們的
> python 產 HTML+離線 js**。

## 1. 三方概觀

| | DeepSeek Trajectory | claude-code-log(我們 transcript 用) | ARCP detail_server |
|---|---|---|---|
| 形態 | React SPA 元件 | python(jinja2)產自足 HTML+inline js | python 產 HTML+vendored vis-timeline |
| 定位 | 單 session 逐 turn 解剖 | 單 session 對話卡片流 | **跨票全域**+單票(狀態視角) |
| 佈局 | **三件套:頂部固定 Overview(50px 時間帶)+中 ledger(序號/事件/內容三欄)+右側 details(320–440px 可拖寬,Input/Output/Timing 頁籤)** | 線性對話卡片長頁+可選 sticky vis-timeline(預設隱藏、ns-resize) | 粗看 /timeline(每票一列色帶)+細看 /ticket(駕駛艙+時間軸抽屜) |
| 泳道 | **3 條固定語意 lane**(0=user/context、1=assistant、2=tool/subtool) | 15+ 訊息類型各一組(user/assistant/tool_use/tool_result/thinking/system/image/sidechain/memory/…) | 單條狀態色帶+事件 emoji 點 |
| 配色 | **三層 token**(static 色階→alias 語意→元件);light/dark 重映射 alias;角色→語意色:user=品牌藍 rgb(65,118,230)、tool=amber rgb(221,134,41)、error=red、context=green | timeline 元件=Material 糖果色硬編碼(light-only);message 卡有 CSS 變數(--user-color 等)+角色色 border | 色票散在 python 字串;明暗主題有、非 token 化 |
| 時長語意 | **assistant span 漸層分 TTFT(淡)/decoding(深)**;in-flight 不畫假 span;未載入歷史「…」不捏造 | span=訊息時長,無分段 | 狀態段(執行/等人/排隊) |
| 聚焦 | **未選中 opacity 0.2、搜尋不中 0.14**;hover/選中雙圈光暈;**拖選區間→ledger 聯動過濾**+選取外 100vw shadow 打暗;滾輪錨點縮放、右鍵平移;**四種橫軸投影**(sequence/duration/time/actual) | 篩選=隱藏;vis-timeline 基本 zoom | ctrl+wheel zoom+tooltip |
| 工程重活 | 虛擬滾動+分頁+串流跟尾(數萬事件級) | 自足單檔(離線可寄) | 離線零 CDN |

## 2. 可學清單(=改造 backlog;性價比排序)

1. **TTFT/decoding 漸層分段**:attempt/assistant span 拆「spawn→首事件」(淡)
   與「首事件→結束」(深)——慢在啟動 vs 生成一眼分。純 CSS gradient。
2. **opacity 聚焦**:過濾/搜尋不隱藏、降到 0.2/0.14;hover/選中=雙層
   box-shadow 光暈(1px 底色圈+2px 主色圈,不動 layout)。
3. **sequence 等寬投影**模式切換:解「長 sleep 壓扁短事件」。
4. **3 條語意泳道**(user/assistant/tool+harness)取代 15 組或單條擠。
5. **配色 token 化**:`--arcp-*` 兩層(static→語意 alias),明暗只重映射;
   角色色參考 Trajectory(品牌藍/amber/red/green)。
6. 拖選區間→下方列表聯動;選取外打暗(±100vw box-shadow 技巧)。
7. 右側 details:頁籤化(Input/Output/Timing)+col-resize 拖寬
   (handle 置 border 上 8px 寬)。
8. hover 500ms 延遲 tooltip;reduced-motion 尊重;in-flight 只畫 start 記號。

## 3. 不搬的

React/虛擬滾動/分頁/串流跟尾(事件量級用不到、離線零依賴是硬需求);
cclog 的 15 組泳道與糖果色(過碎、light-only)。保留我們獨有的跨票全域
視角與狀態色帶語意。

## 4. 落地路線(定案後寫入 BACKLOG)

cclog 是 **zero-diff vendor**(NOTICE.md 約束)——不改它的模板。戰場:
- **A. detail_server ticket 頁**(自有碼):conversation+事件時間軸改
  Trajectory 三件套排版。
- **B. 自寫 `trajectory.html` 產生器**(python 從 aN.events.jsonl 渲染,
  與 cclog 的 final.html **並存**於 transcript 目錄):完整抄 Trajectory
  排版,純離線、自足單檔。
