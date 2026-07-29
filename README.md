# 台灣股市去槓桿壓力儀表板

仿照「[韓國股市去槓桿壓力儀表板](https://kidd0368.github.io/)」的架構重製的台股版本。同一套
`fetch → compute → build → commit` pipeline，數據源改為台灣公開資料（FinMind 開放 API）。

## 這次交付了什麼

- `fetch_data.py` — 用 FinMind 免費 API 抓取全市場融資融券餘額與加權指數(TAIEX)日線，
  整理成 `data/tw_leverage_bulk.json`。只用 Python 標準函式庫（urllib），不需要 pip install 任何套件。
  - `compute_indicators.py` — 計算去槓桿壓力綜合指數與所有子指標，輸出 `out/indicators.json`。
  - `dashboard_template.html` — 儀表板前端（純 HTML/CSS/原生 JS，無外部套件相依）。
  - `build_dashboard.py` — 把 indicators.json 內嵌進 template，產出最終 `index.html`。
  - `update.yml` — GitHub Actions 排程設定，每個交易日台北時間 18:30 自動跑一次並 commit。

  ## 如何部署成像韓版一樣「每天自動更新」的網站

  1. 在 GitHub 開一個新的 public repo，命名為 <你的帳號>.github.io（GitHub Pages 的個人網站規則）。
  2. 把這幾個檔案放進 repo 根目錄，update.yml 放到 .github/workflows/update.yml 路徑下。
  3. Settings → Pages → 確認來源是 main 分支根目錄。
  4. Settings → Actions → General → Workflow permissions，選「Read and write permissions」。
  5. 手動觸發一次：Actions 頁籤 → 選 workflow → Run workflow，確認能正常跑完、index.html 有被 commit。

  ## 與韓版方法論的關鍵差異（誠實揭露）

  - 沒有逐日公開的斷頭/追繳/整戶維持率數據：改用「融資餘額單日/五日降幅」代理，頁面上有明顯註記提醒不要跟韓股斷頭金額直接類比。
  - 沒有逐日公開的投資人預託金數據：不設「融資/預託金比」子指標，權重已重新分配到其餘指標。
  - 只涵蓋 TWSE 上市（加權指數），暫不含上櫃市場。
  - 波動率用 20 日已實現波動率年化。
  - 出清進度的「基期」改用規則化定義（峰值前一段視窗內的谷底），不像韓版引用特定新聞事件日期。

  ## 這不是投資建議

  本儀表板僅供研究/資訊參考，不構成任何投資建議。數據可能有延遲或誤差，使用前請自行查證。
  
