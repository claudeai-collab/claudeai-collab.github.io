# -*- coding: utf-8 -*-
"""
台灣股市去槓桿壓力分析 — 指標計算引擎
輸入: data/tw_leverage_bulk.json (fetch_data.py 產出, 單位見各欄位註記)
輸出: out/indicators.json (儀表板數據) + CSV 匯出

方法論基礎: 仿照 KOFIA/KRX 版「韓國股市去槓桿壓力儀表板」之四維度框架
（槓桿水位 / 出清進度 / 被動賣壓 / 市場應激），依台灣可取得之公開數據重新設計。

**與韓版的關鍵差異（誠實揭露，見 fetch_spec.md 與 README）**：
1. 台灣沒有「融資整戶維持率」「追繳」「斷頭（反對賣買）」的逐日公開數據，
   故「被動賣壓」子指標改用「融資餘額單日/五日減幅」作為代理指標，
   而非真實強制平倉金額。這是使用者明確選定的替代方案。
2. 台灣沒有「投資人預託金」的逐日公開數據，故不設「融資/預託金比」子指標，
   權重已重新分配到其餘子指標。
3. 目前只涵蓋 TWSE 上市（加權指數）集中市場，不含 TPEx 上櫃市場——
   FinMind 未提供對應的上櫃大盤指數 data_id，待未來版本補上。
4. 波動率以 20 日已實現波動率年化計算（無官方 VIX 類指數可用）。
"""
import json, math, sys, csv, os
from datetime import datetime, timezone

CONFIG = {
    # 出清進度的「基期」= 回看視窗內的谷底，而非特定新聞事件日期
    # （台灣版不像韓版有明確引用的「AI 硬體行情啟動日」，故採規則化定義，可自行調整）
    "peak_lookback_days": 400,       # 從最近 400 個交易日內找槓桿峰值
    "baseline_lookback_days": 800,   # 峰值之前，再往回看這麼多交易日找槓桿谷底作為基期
    "pctl_window": 1250,             # ≈5年，滾動百分位視窗
    "rv_window": 20,                 # 已實現波動率天數
    "momentum_ma": 5,                # 融資動能觀察天數
    "display_daily_from": "20230101",
    "weights": {
        "lvl_margin_pctl": 20.0,      # 槓桿水位：融資餘額(金額)5年百分位
        "lvl_turnover_pctl": 10.0,    # 槓桿水位：融資餘額/成交值 比 百分位
        "unwind_remaining": 25.0,     # 出清進度：未出清比例
        "momentum": 10.0,             # 出清進度：融資5日動能
        "forced_proxy_pctl": 15.0,    # 被動賣壓代理：融資單日/五日減幅 百分位
        "vol_pctl": 10.0,             # 市場應激：20日已實現波動率百分位
        "turnover_pctl": 10.0,        # 市場應激：成交值百分位
    },
    "signal2_manual": {"status": "watch", "note": "請依當前總經/資金面新聞人工更新"},
    "signal3_manual": {"status": "watch", "note": "請依金管會/證交所最新信用交易管制措施人工更新"},
}


def num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def last_valid(arr, dates=None):
    for i in range(len(arr) - 1, -1, -1):
        if arr[i] is not None:
            return (arr[i], dates[i] if dates else None, i)
    return (None, None, None)


def pctl_of_last(window_vals, v):
    vals = [x for x in window_vals if x is not None]
    if v is None or len(vals) < 60:
        return None
    below = sum(1 for x in vals if x <= v)
    return round(100.0 * below / len(vals), 1)


def rolling_pctl(series, window):
    out = [None] * len(series)
    for i, v in enumerate(series):
        lo = max(0, i - window + 1)
        out[i] = pctl_of_last(series[lo:i + 1], v)
    return out


def compute(bulk):
    credit_m = {r[0]: r for r in bulk.get("credit", [])}
    market_rows = sorted(bulk.get("market", []), key=lambda r: r[0])

    dates, S = [], {k: [] for k in [
        "margin_total", "margin_shares", "short_shares",
        "taiex_idx", "turn_val", "turn_heat", "taiex_ret",
        "margin_turnover_ratio",
    ]}

    # 主軸 = 大盤行情日期；信用（融資）數據若缺（假日錯位等）以 null 呈現
    for m in market_rows:
        d, close, vol, val = m[0], num(m[1]), num(m[2]), num(m[3])
        if close is None:
            continue
        c = credit_m.get(d)
        margin_money = num(c[1]) if c else None      # 元
        margin_shares = num(c[2]) if c else None      # 張
        short_shares = num(c[3]) if c else None        # 張
        dates.append(d)
        S["margin_total"].append(None if margin_money is None else margin_money / 1e8)   # 億元
        S["margin_shares"].append(margin_shares)
        S["short_shares"].append(short_shares)
        S["taiex_idx"].append(close)
        S["turn_val"].append(None if val is None else val / 1e8)   # 億元
        S["turn_heat"].append(None if not val else val / 1e8)      # 成交值(億元)本身作為熱度代理
        n = len(S["taiex_idx"])
        prev = S["taiex_idx"][n - 2] if n >= 2 else None
        S["taiex_ret"].append(None if (not prev or close is None) else math.log(close / prev))
        S["margin_turnover_ratio"].append(
            None if (margin_money is None or not val) else round(margin_money / val, 3))

    n = len(dates)
    min_days = 20 if os.environ.get("KL_ALLOW_SHORT") else 40
    if n < min_days:
        raise SystemExit(f"數據不足: 僅 {n} 個交易日")

    # 回撤 / 已實現波動率
    W52 = min(252, n)
    dd = []
    for i, v in enumerate(S["taiex_idx"]):
        m = max(S["taiex_idx"][max(0, i - W52 + 1):i + 1])
        dd.append(round(100 * (v / m - 1), 2) if m else None)
    seg52 = S["taiex_idx"][max(0, n - W52):]
    hi52 = max(seg52)
    hi52_date = dates[max(0, n - W52) + seg52.index(hi52)]

    rv20 = [None] * n
    w = CONFIG["rv_window"]
    for i in range(n):
        if i >= w:
            rets = [x for x in S["taiex_ret"][i - w + 1:i + 1] if x is not None]
            if len(rets) >= w - 2:
                mrt = sum(rets) / len(rets)
                var = sum((x - mrt) ** 2 for x in rets) / max(1, len(rets) - 1)
                rv20[i] = round(100 * math.sqrt(var * 252), 1)

    # 融資單日降幅（被動賣壓代理）：只取下降的部分（正數＝當日減少百分比）
    margin_drop_pct = [None] * n
    for i in range(1, n):
        a, b = S["margin_total"][i - 1], S["margin_total"][i]
        if a and b is not None and a > 0:
            chg = 100 * (b / a - 1)
            margin_drop_pct[i] = round(max(0.0, -chg), 3)
    ma = CONFIG["momentum_ma"]

    def sma(arr):
        out = [None] * n
        for i in range(n):
            win = [x for x in arr[max(0, i - ma + 1):i + 1] if x is not None]
            out[i] = round(sum(win) / len(win), 3) if win else None
        return out
    margin_drop_ma = sma(margin_drop_pct)

    # 滾動百分位
    W = CONFIG["pctl_window"]
    P = {
        "margin_total": rolling_pctl(S["margin_total"], W),
        "margin_turnover_ratio": rolling_pctl(S["margin_turnover_ratio"], W),
        "margin_drop_ma": rolling_pctl(margin_drop_ma, W),
        "rv20": rolling_pctl(rv20, W),
        "turn_val": rolling_pctl(S["turn_val"], W),
    }
    partial = n < 600
    if partial:
        for key in P:
            P[key] = [None] * n

    def last_pctl(key):
        v, _, _ = last_valid(P[key], dates)
        return v

    def series_peak(arr):
        pv, pd = None, None
        for i, v in enumerate(arr):
            if v is not None and (pv is None or v > pv):
                pv, pd = v, dates[i]
        return pv, pd

    # 出清進度 U：以「回看視窗內的槓桿峰值」與「峰值之前的谷底」為基準
    # （台灣版無明確引用之新聞基期，改採規則化：峰值前 baseline_lookback_days 內的最低點）
    mt_valid = [(i, v) for i, v in enumerate(S["margin_total"]) if v is not None]
    if not mt_valid:
        raise SystemExit("融資餘額序列全空，無法計算出清進度")
    lb = min(CONFIG["peak_lookback_days"], n)
    seg = [(i, v) for i, v in mt_valid if i >= n - lb]
    peak_i, peak_v = max(seg, key=lambda t: t[1])
    blb = CONFIG["baseline_lookback_days"]
    bseg = [(i, v) for i, v in mt_valid if peak_i - blb <= i <= peak_i]
    if not bseg:
        bseg = mt_valid[:1]
    bi, base_v = min(bseg, key=lambda t: t[1])
    cur_i, cur = mt_valid[-1]
    U = 1.0 if peak_v <= base_v else max(0.0, min(1.0, (peak_v - cur) / (peak_v - base_v)))
    tail = [v for _, v in mt_valid[-6:]]
    d5 = (tail[-1] / tail[0] - 1) if len(tail) == 6 and tail[0] else None

    asof = dates[-1]
    _, asof_credit, _ = last_valid(S["margin_total"], dates)

    drop_pk, drop_pk_d = series_peak(margin_drop_pct)

    # 綜合壓力指數
    Wt = dict(CONFIG["weights"])

    def lp(key):
        v = last_pctl(key)
        return 50.0 if v is None else v
    mom_score = 1.0 if (d5 is None or d5 > 0.01) else (0.5 if d5 > -0.01 else 0.25)
    parts = {
        "槓桿水位·融資餘額百分位": Wt["lvl_margin_pctl"] * lp("margin_total") / 100,
        "槓桿水位·融資/成交值百分位": Wt["lvl_turnover_pctl"] * lp("margin_turnover_ratio") / 100,
        "出清進度·未出清比例": Wt["unwind_remaining"] * (1 - U),
        "出清進度·融資動能": Wt["momentum"] * mom_score,
        "被動賣壓代理·融資降幅百分位": Wt["forced_proxy_pctl"] * lp("margin_drop_ma") / 100,
        "市場應激·波動率": Wt["vol_pctl"] * lp("rv20") / 100,
        "市場應激·成交熱度": Wt["turnover_pctl"] * lp("turn_val") / 100,
    }
    score = round(sum(parts.values()), 1)
    zone = ("high", "≥70 高壓：去化初中期") if score >= 70 else \
           ("mid", "45-70 中後期：去化進行中") if score >= 45 else \
           ("late", "25-45 尾聲：接近出清") if score >= 25 else ("done", "<25 大致出清")

    dp_now = last_pctl("margin_drop_ma")
    rv_now = last_pctl("rv20")
    if (d5 is not None and d5 > -0.01) and (dp_now is None or dp_now < 50) and (rv_now is None or rv_now < 70):
        stage, stage_label = 3, "第三階段：融資企穩、降幅代理指標回落，市場重新回歸基本面定價"
    elif U < 0.25 and (d5 is None or d5 < -0.015):
        stage, stage_label = 1, "第一階段：價格快跌，融資帳戶集中觸發追加保證金（估計）"
    else:
        stage = 2
        stage_label = "第二階段" + ("後期" if U >= 0.6 else "") + "：融資去化持續進行、賣壓仍大的時期"

    cur_month = dates[-1][:6]
    drop_mtd = round(sum(v for i, v in enumerate(margin_drop_pct) if v is not None and dates[i][:6] == cur_month), 2)

    s1_ok_drop = (last_pctl("margin_drop_ma") or 50) < 50
    s1_ok_margin = d5 is not None and d5 > -0.01
    s1 = "green" if (s1_ok_drop and s1_ok_margin) else ("yellow" if (s1_ok_drop or s1_ok_margin) else "red")

    keep, dfrom = [], CONFIG["display_daily_from"]
    for i, d in enumerate(dates):
        keep.append(i)  # 台灣版資料量較小，暫不降採樣

    def pick(arr):
        return [arr[i] for i in keep]

    def latest_of(key):
        v, _, _ = last_valid(S[key], dates)
        return v
    latest = {k: latest_of(k) for k in
               ["margin_total", "margin_shares", "short_shares", "taiex_idx", "turn_val", "margin_turnover_ratio"]}

    out = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "sample": bool(bulk.get("meta", {}).get("sample")),
        "partial": partial,
        "pctl_source": ("rolling" if not partial else None),
        "data_from": dates[0],
        "asof": asof,
        "asof_market": asof,
        "asof_credit": asof_credit,
        "n_days_total": n,
        "config": {"pctl_window_days": W, "weights": Wt},
        "dates": pick(dates),
        "daily_from": dfrom,
        "series": {
            "margin_total": pick(S["margin_total"]),
            "margin_shares": pick(S["margin_shares"]),
            "short_shares": pick(S["short_shares"]),
            "taiex_idx": pick(S["taiex_idx"]),
            "taiex_dd": pick(dd),
            "rv20": pick(rv20),
            "turn_val": pick(S["turn_val"]),
            "margin_turnover_ratio": pick(S["margin_turnover_ratio"]),
            "margin_drop_pct": pick(margin_drop_pct),
            "margin_drop_ma": pick(margin_drop_ma),
        },
        "latest": latest,
        "latest_extra": {
            "taiex_dd": dd[-1], "taiex_hi52": round(hi52, 2), "taiex_hi52_date": hi52_date,
            "rv20": last_valid(rv20)[0],
            "margin_drop_ma": last_valid(margin_drop_ma)[0],
            "margin_d5_pct": None if d5 is None else round(100 * d5, 2),
            "margin_drop_peak": [drop_pk, drop_pk_d],
            "pctl": {k: last_pctl(k) for k in P},
        },
        "unwind": {"peak": round(peak_v, 1), "peak_date": dates[peak_i],
                   "baseline": round(base_v, 1), "baseline_date": dates[bi],
                   "current": round(cur, 1), "U": round(U, 3),
                   "excess_peak": round(peak_v - base_v, 1), "excess_now": round(cur - base_v, 1)},
        "composite": {"score": score, "zone": zone[0], "zone_label": zone[1],
                      "parts": {k: round(v, 2) for k, v in parts.items()}},
        "stage": {"n": stage, "label": stage_label,
                  "drop_mtd": drop_mtd, "drop_pctl": dp_now, "rv_pctl": rv_now},
        "signals": {
            "s1": {"status": s1, "label": "技術性賣壓衰竭（代理指標）",
                   "detail": ("融資降幅5日均百分位 " + (str(last_pctl('margin_drop_ma')) if not partial else "待完整歷史")
                              + f"｜融資5日 {('' if d5 is None else f'{d5*100:+.1f}%')}")},
            "s2": {"status": CONFIG["signal2_manual"]["status"], "label": "外部催化劑落地",
                   "detail": CONFIG["signal2_manual"]["note"] + "（人工旗標）"},
            "s3": {"status": CONFIG["signal3_manual"]["status"], "label": "監管干預力度",
                   "detail": CONFIG["signal3_manual"]["note"] + "（人工旗標）"},
        },
        "etf": {"enabled": False, "note": "槓桿ETF觀察（如元大台灣50正2 00631L）待補：fetch_data.py 已含抓取邏輯，需累積時序後啟用"},
    }
    return out


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "data/tw_leverage_bulk.json"
    with open(src, encoding="utf-8") as f:
        bulk = json.load(f)
    out = compute(bulk)
    os.makedirs("out", exist_ok=True)
    with open("out/indicators.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open("out/tw_leverage_daily.csv", "w", newline="", encoding="utf-8-sig") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["date"] + list(out["series"].keys()))
        for i, d in enumerate(out["dates"]):
            wcsv.writerow([d] + [out["series"][k][i] for k in out["series"]])
    print(f"OK asof={out['asof']} days={out['n_days_total']} "
          f"score={out['composite']['score']} zone={out['composite']['zone']} U={out['unwind']['U']}")


if __name__ == "__main__":
    main()
