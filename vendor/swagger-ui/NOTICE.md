# NOTICE — vendored Swagger UI

- **上游**:https://github.com/swagger-api/swagger-ui(npm 套件 `swagger-ui-dist`)
- **授權**:Apache-2.0
- **版本**:5.32.12(vendor 於 2026-08-07,`npm pack swagger-ui-dist@5.32.12`)
- **vendor 範圍**(離線/內網自足所需最小集):
  - `swagger-ui.css`(178KB,無外部 `url()` 引用——字型/圖示皆內嵌 data:)
  - `swagger-ui-bundle.js`(1.5MB,單檔含全部相依——React/Redux/highlight.js
    等已 bundle;檔內的 http(s) 字串皆為文件/RFC 連結,非執行期載入)
  - `swagger-ui-bundle.js.LICENSE.txt`(bundle 內第三方元件的授權彙整)
- **未 vendor**:`swagger-ui-standalone-preset.js`(topbar/URL 輸入列,內網不需)、
  `.map` sourcemap、預設 `index.html`(改用 ARCP 自寫的 `/docs` HTML)。
- **上游碼修改**:無(zero-diff vendor)。載入頁 `/docs` 由 detail_server 產生,
  指向本地 `/swagger-assets/<file>`,並讀本地 `/openapi.json`(ARCP 自寫規格)。
- **升級**:重跑 `npm pack swagger-ui-dist@<新版>`,覆蓋上述三檔即可。

## 為什麼選它(W6.5 評估結論)

需求:內網/離線給 REST API 一個可瀏覽、可「Try it out」的文件頁。手寫 HTML
維護成本高且無互動;Swagger UI 是 OpenAPI 生態事實標準,`swagger-ui-dist` 是
官方預編 bundle、單檔自足、vendor 後完全離線可用。CSP 仍對 `/docs` 只放行同源 +
內嵌,杜絕任何外連。
