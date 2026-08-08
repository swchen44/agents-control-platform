# DESIGN_isolation — 執行隔離可插拔(D1/W22,介面先行)

> 使用者定調(2026-08-04):「現在就做,但先不驗。未來我們會用在 linux/windows/
> macos,優先用 OS 提供方,也給選項是 docker,用設定檔給使用者選擇。」
> 本檔 = 介面 + 各 OS 路線;**本波只實作解析,不做新隔離**(唯一真的會啟用的
> 仍是既有 macOS seatbelt,W1 前已實測)。

## 設定介面

```yaml
agent:
  isolation:
    provider: auto     # auto | seatbelt | landlock | appcontainer | docker | none
```

- **auto**(建議預設):依 OS 選提供方——darwin→seatbelt、linux→landlock、
  windows→appcontainer;無對應 → none。
- 舊寫法 `os_sandbox: true` == `provider: auto`(向後相容;新 config 請用
  isolation 區塊,os_sandbox 視為 deprecated)。
- 白名單在 load 時 fail-fast(壞值死在啟動);**未實作的 provider 接受設定、
  不啟用 + WARNING log**(使用者要求先不驗——設定檔先能寫,行為後補)。
- codex 例外:codex CLI 自帶 `--sandbox`(workspace-write 等),OS 層隔離對它
  本就 no-op;isolation 主要作用於 claude(無內建 OS sandbox)。

## 各 OS 提供方路線(未來實作備忘)

| provider | OS | 機制 | 狀態 |
|---|---|---|---|
| seatbelt | macOS | `sandbox-exec` profile(限制檔案寫入到 workspace) | ✅ 已實作+實測(workspace 可寫、/tmp 擋) |
| landlock | Linux | Landlock LSM(kernel 5.13+,非特權 fs 限制;`landlockctl` 或 prctl 包裝) | 預留 |
| appcontainer | Windows | AppContainer / restricted token | 預留 |
| docker | 跨平台 | 容器內跑 CLI;**邊界**:native resume 綁 cwd → volume mount 要穩定路徑;CLI 憑證(~/.claude、codex auth)要 mount 或 in-container login;冷啟成本 | 預留(選項,不是預設——OS 提供方優先) |
| none | — | 不隔離 | ✅ |

## 解析規則(arcp_harness/isolation.py)

```
requested = agent.isolation.provider || (os_sandbox ? auto : none)
effective = requested==auto ? by_platform[sys.platform] : requested
若 effective 未實作或平台不符 → none + WARNING(不擋、不炸)
inner_runner:job.os_sandbox = (effective == "seatbelt")   # runner 端欄位不變
```
