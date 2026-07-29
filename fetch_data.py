# -*- coding: utf-8 -*-
"""
台股去槓桿數據抓取（FinMind 開放 API，免登入、免金鑰）

**重要**：此腳本需要對外網路連線，設計為在 GitHub Actions runner 上執行
（對外網路無限制）。開發沙盒環境的對外網路為白名單制，無法在本機容器內
直接測試 api.finmindtrade.com，此為刻意的設計約束（與韓版原專案 fetch_spec.md
所述完全相同的處境）——請於 Actions 首次執行後，檢查 out/indicators.json
與 data/tw_leverage_bulk.json 是否正常產出來驗證。

數據源：
- TaiwanStockTotalMarginPurchaseShortSale：全市場融資融券餘額（日度）
  → 三種 name：MarginPurchase(張)/ShortSale(張)/MarginPurchaseMoney(元)
- TaiwanStockPrice(data_id=TAIEX)：加權指數日線（OHLC、成交量、成交值）
- TaiwanStockMarginPurchaseShortSale(data_id=00631L)：
  元大台灣50正2（最主要的台股槓桿ETF）融資餘額，供未來「槓桿ETF觀察」卡片使用

FinMind 免費額度有速率限制，單次抓取全歷史（2010至今，約4000+交易日）
一般 1-2 個請求即可（伺服器端未強制分頁），若遇到限流請自行加上重試/延遲。

管線：
    python3 fetch_data.py            → data/tw_leverage_bulk.json
                                        （同時嘗試 data/tw_etf_00631L.json，失敗不影響主流程）
    python3 compute_indicators.py data/tw_leverage_bulk.json  → out/indicators.json
    python3 build_dashboard.py       → out/tw_deleverage_dashboard.html
    cp out/tw_deleverage_dashboard.html index.html
"""
import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

API = "https://api.finmindtrade.com/api/v4/data"
START_DATE = "2010-01-01"   # FinMind 融資融券資料起始約在此之後即有穩定數據
TOKEN = os.environ.get("FINMIND_TOKEN", "")  # 選配：註冊 FinMind 可取得 token 提高速率限制


def fetch(dataset, data_id=None, start_date=START_DATE, end_date=None, retries=4):
    params = {"dataset": dataset, "start_date": start_date}
    if data_id:
        params["data_id"] = data_id
    if end_date:
        params["end_date"] = end_date
    if TOKEN:
        params["token"] = TOKEN
    from urllib.parse import urlencode
    url = API + "?" + urlencode(params)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tw-deleverage-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") != 200:
                raise RuntimeError(f"FinMind API error: {data.get('msg')}")
            return data.get("data", [])
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"抓取失敗 dataset={dataset} data_id={data_id}: {last_err}")


def build_credit_rows(margin_raw):
    """把三種 name 的紀錄依日期併成 [date, margin_money, margin_shares, short_shares]。"""
    by_date = {}
    for r in margin_raw:
        d = r.get("date")
        if not d:
            continue
        row = by_date.setdefault(d, [d, None, None, None])
        name = r.get("name")
        bal = r.get("TodayBalance")
        if name == "MarginPurchaseMoney":
            row[1] = bal
        elif name == "MarginPurchase":
            row[2] = bal
        elif name == "ShortSale":
            row[3] = bal
    return [by_date[d] for d in sorted(by_date)]


def build_market_rows(price_raw):
    rows = []
    for r in price_raw:
        d = r.get("date")
        close = r.get("close")
        if not d or close is None:
            continue
        rows.append([d, close, r.get("Trading_Volume"), r.get("Trading_money")])
    rows.sort(key=lambda r: r[0])
    return rows


def main():
    os.makedirs("data", exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print("抓取全市場融資融券餘額...", file=sys.stderr)
    margin_raw = fetch("TaiwanStockTotalMarginPurchaseShortSale", end_date=today)
    print(f"  {len(margin_raw)} 筆原始紀錄", file=sys.stderr)

    print("抓取加權指數(TAIEX)日線...", file=sys.stderr)
    price_raw = fetch("TaiwanStockPrice", data_id="TAIEX", end_date=today)
    print(f"  {len(price_raw)} 筆原始紀錄", file=sys.stderr)

    bulk = {
        "meta": {"source": "FinMind", "generated": datetime.now(timezone.utc).isoformat(), "sample": False},
        "credit": build_credit_rows(margin_raw),
        "market": build_market_rows(price_raw),
    }
    with open("data/tw_leverage_bulk.json", "w", encoding="utf-8") as f:
        json.dump(bulk, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK -> data/tw_leverage_bulk.json (credit={len(bulk['credit'])}, market={len(bulk['market'])})")

    # 槓桿ETF觀察（選配，失敗不影響主流程）——元大台灣50正2 00631L
    try:
        print("抓取00631L(元大台灣50正2)融資餘額與價格...", file=sys.stderr)
        etf_margin = fetch("TaiwanStockMarginPurchaseShortSale", data_id="00631L", end_date=today)
        etf_price = fetch("TaiwanStockPrice", data_id="00631L", end_date=today)
        etf_price_m = {r["date"]: r for r in etf_price if r.get("date")}
        etf_rows = []
        for r in etf_margin:
            d = r.get("date")
            p = etf_price_m.get(d)
            etf_rows.append([d, r.get("MarginPurchaseTodayBalance"),
                              p.get("close") if p else None])
        etf_rows.sort(key=lambda r: r[0])
        with open("data/tw_etf_00631L.json", "w", encoding="utf-8") as f:
            json.dump({"rows": etf_rows, "cols": ["date", "margin_lots", "close"]}, f, ensure_ascii=False)
        print(f"OK -> data/tw_etf_00631L.json ({len(etf_rows)} 筆)")
    except Exception as e:  # noqa: BLE001
        print(f"WARN: 00631L 抓取失敗，略過（不影響主指標）: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
