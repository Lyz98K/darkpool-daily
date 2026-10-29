# -*- coding: utf-8 -*-
"""暗盘资金扫描器 v2 — 明暗盘资金背离体检 + 板块对比看板 + 个股深挖。

数据源: 东方财富「暗盘资金」公开接口(免费, 免 key, 无需登录)
  GET https://quotederivates.eastmoney.com/datacenter/darktrade
    version=102  全市场快照(单次返回全部 5600+ 行)
    version=100  个股榜(分页, sortflag 6=暗盘 7=明盘 8=明暗合计)
    version=101  板块榜(market=90 + datetype=2行业/3概念)
  GET https://quotederivates.eastmoney.com/datacenter/darktradeone
    个股暗盘概况(按日回看, 可拼约 112 天历史序列)

口径声明: 暗盘资金是平台量化模型的**估算口径**, 非交易所官方数据, 仅供参考。
响应体为 GBK 编码 JSON, 字段是数字字符串键("3"市场 "4"代码 "6"暗盘净流入(元)
"7"明盘净流入 "8"明暗合计 "11"活跃度0~1 "13"价×1000 "14"涨幅0~1 "16"名称)。

用法:
  pip install requests
  python darkpool_scan.py                  # 最新交易日全市场报告
  python darkpool_scan.py --date 20260828  # 回看指定交易日(约 112 天内)
  python darkpool_scan.py --code 600519    # 个股深挖(明暗盘日度历史四联图)
  python darkpool_scan.py --no-open        # 不自动打开浏览器

双输出: 终端打印 + scripts/research_out/暗盘资金_{date}.html(深色单文件报告)。
仅依赖 requests。
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import time
import webbrowser
from datetime import date as _date, datetime, timedelta
from pathlib import Path

import requests

BASE = "https://quotederivates.eastmoney.com/datacenter"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)",
    "rnProjectId": "emrn.GrayMarketRank",
}

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "research_out"
OUT_DIR.mkdir(exist_ok=True)


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _decode(r: requests.Response) -> dict:
    """GBK/UTF-8 自适应解码(个股榜 GBK, 板块榜 UTF-8)。"""
    text = None
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "gbk" in ctype or "gb18030" in ctype:
        text = r.content.decode("gb18030", errors="replace")
    else:
        try:
            text = r.content.decode("utf-8")
        except UnicodeDecodeError:
            text = r.content.decode("gb18030", errors="replace")
    data = json.loads(text)
    if data.get("errid") != 0:
        raise RuntimeError(f"接口返回 errid={data.get('errid')} errmsg={data.get('errmsg')}")
    return data


def _get(path: str, params: dict, retry: int = 3, sleep: float = 1.0) -> dict:
    last = None
    for _ in range(retry):
        try:
            r = requests.get(BASE + path, params=params, headers=HEADERS, timeout=12)
            return _decode(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(sleep)
    raise last


def _market_of(code: str) -> str:
    return "1" if code.startswith(("6", "90")) else "0"


def _row(row: dict) -> dict:
    market = row.get("3")
    out = {
        "market": market,
        "code": row.get("4"),
        "data_time": str(row.get("5", "")).zfill(6),
        "dark_net": row.get("6") or 0,          # 暗盘净流入(元)
        "bright_net": row.get("7") or 0,        # 明盘净流入(元)
        "total_net": row.get("8") or 0,         # 主力净流入(含暗盘)
        "dark_active": row.get("11") or 0,      # 暗盘活跃度 0~1
        "price": (row.get("13") or 0) / 1000,
        "pct_chg": (row.get("14") or 0) * 100,  # → %
        "name": row.get("16") or "",
        "rank": row.get("21"),
    }
    if market == 90:  # 板块行
        out.update({
            "inflow_count": row.get("9"),
            "outflow_count": row.get("10"),
            "leader_name": row.get("15") or "",
            "leader_code": row.get("20") or "",
        })
    else:
        out.update({
            "industry": row.get("17") or "",
            "concept": row.get("18") or "",
        })
    return out


LAST_DATA_DATE = ""  # fetch_rank 从个股榜响应顶层 "1" 字段捕获的实际数据日期


def fetch_rank(rank_type: str, date: str, page: int, size: int = 100,
               sortflag: int = 6, desc: int = 1) -> tuple[int, list[dict]]:
    if rank_type == "stock":
        version, market, datetype = 100, "", ""
    elif rank_type == "industry":
        version, market, datetype = 101, "90", "2"
    else:  # concept
        version, market, datetype = 101, "90", "3"
    data = _get("/darktrade", {
        "version": version, "cver": 100, "date": date,
        "StartPage": page, "NumPerPage": size,
        "sortflag": sortflag, "desc": desc,
        "market": market, "datetype": datetype,
    })
    global LAST_DATA_DATE
    if rank_type == "stock" and data.get("1"):
        LAST_DATA_DATE = str(data["1"])
    return data.get("2", 0), [_row(r) for r in (data.get("data") or [])]


def fetch_full_snapshot(date: str) -> tuple[str, list[dict]]:
    """返回 (实际数据日期 yyyymmdd, rows)。"""
    data = _get("/darktrade", {
        "version": 102, "cver": 100, "date": date,
        "StartPage": 1, "NumPerPage": 100, "sortflag": 6, "desc": 1,
        "market": "", "datetype": "",
    })
    return str(data.get("1") or ""), [_row(r) for r in (data.get("data") or [])]


def fetch_stock_one(code: str, date: str = "") -> dict | None:
    """darktradeone 个股单日概况(按 date 回看)。"""
    data = _get("/darktradeone", {
        "version": 100, "cver": 100, "date": date,
        "market": _market_of(code), "code": code,
        "StartPage": 1, "NumPerPage": 30, "sortflag": 6, "desc": 1,
    })
    rows = data.get("data") or []
    if not rows:
        return None
    r = _row(rows[0])
    r["real_date"] = str(data.get("1") or "")
    return r


def fetch_stock_history(code: str, days: int = 30) -> list[dict]:
    """个股近 N 个交易日暗盘历史(逐日回看拼装, 服务端自动对齐非交易日)。"""
    seen, hist = set(), []
    d = _date.today()
    while len(hist) < days:
        if d.weekday() < 5:
            ds = d.strftime("%Y%m%d")
            try:
                r = fetch_stock_one(code, ds)
            except RuntimeError:
                break  # errid=-2 超出回看边界
            if r and r["real_date"] not in seen:
                seen.add(r["real_date"])
                hist.append(r)
            time.sleep(0.25)
        d -= timedelta(days=1)
    hist.reverse()
    return hist


def sina_klines(code: str, n: int = 60) -> list[dict]:
    """新浪日 K(价格+成交量, 供个股深挖叠加)。失败返回 []。"""
    import re as _re
    prefix = "sh" if code[0] in "69" else "sz"
    try:
        r = requests.get(
            f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_d=/CN_MarketDataService.getKLineData"
            f"?symbol={prefix}{code}&scale=240&ma=no&datalen={n}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}, timeout=12)
        m = _re.search(r"var\s+_d\s*=\s*\((\[.*?\])\)", r.text, _re.DOTALL)
        if m:
            return [{"date": x["day"][:10], "close": float(x["close"]),
                     "volume": float(x["volume"])} for x in json.loads(m.group(1))]
    except Exception:  # noqa: BLE001
        pass
    return []


def yi(v: float) -> str:
    av = abs(v)
    if av >= 1e8:
        return f"{v / 1e8:+.2f}亿"
    if av >= 1e4:
        return f"{v / 1e4:+.0f}万"
    return f"{v:+.0f}"


# ================================================================
# 数据装配
# ================================================================

def gather(date: str) -> dict:
    log("拉取全市场暗盘快照(version=102)…")
    real_date, snap = fetch_full_snapshot(date)
    snap_dt = max((r["data_time"] for r in snap), default="")

    log("拉取暗盘净流入榜 top300…")
    dark_top: list[dict] = []
    total_stocks = 0
    for p in range(1, 4):
        total_stocks, rows = fetch_rank("stock", date, p, 100, sortflag=6, desc=1)
        dark_top.extend(rows)
        time.sleep(0.3)

    log("拉取明盘净流入榜 top200…")
    bright_top: list[dict] = []
    for p in range(1, 3):
        _, rows = fetch_rank("stock", date, p, 100, sortflag=7, desc=1)
        bright_top.extend(rows)
        time.sleep(0.3)

    log("拉取明暗合计榜(个股 top100 + 行业/概念全量)…")
    _, stock_total = fetch_rank("stock", date, 1, 100, sortflag=8, desc=1)
    time.sleep(0.3)
    _, ind_all = fetch_rank("industry", date, 1, 100, sortflag=8, desc=1)
    time.sleep(0.3)
    _, con_all = fetch_rank("concept", date, 1, 100, sortflag=8, desc=1)

    # 全市场统计(快照口径, 含北交所/沪B)
    n = len(snap) or 1
    dark_sum = sum(r["dark_net"] for r in snap)
    bright_sum = sum(r["bright_net"] for r in snap)
    pos_ratio = sum(1 for r in snap if r["dark_net"] > 0) / n

    # 背离榜 A: 暗进明出(暗盘大额净流入 且 明盘净流出)
    div_a = sorted(
        (r for r in dark_top if r["dark_net"] > 0 and r["bright_net"] < 0),
        key=lambda r: r["dark_net"], reverse=True)[:15]
    # 背离榜 B: 明进暗出(明盘大额净流入 且 暗盘净流出) —— 公开通道拉抬、隐蔽通道撤退
    div_b = sorted(
        (r for r in bright_top if r["bright_net"] > 0 and r["dark_net"] < 0),
        key=lambda r: r["bright_net"], reverse=True)[:15]

    return {
        "date": LAST_DATA_DATE or real_date or date or "最新交易日",
        "snap_time": snap_dt,
        "total_stocks": total_stocks,
        "snap_count": len(snap),
        "dark_sum": dark_sum,
        "bright_sum": bright_sum,
        "pos_ratio": pos_ratio,
        "dark_top": dark_top[:20],
        "bright_top": bright_top[:10],
        "div_a": div_a,
        "div_b": div_b,
        "industry": ind_all,
        "concept": con_all,
        "stock_total": stock_total,
    }


def gather_stock(code: str) -> dict:
    """个股深挖: 暗盘日度历史 + 新浪日K叠加。"""
    log(f"拉取 {code} 暗盘日度历史(约 30 个交易日)…")
    hist = fetch_stock_history(code, days=30)
    kl = sina_klines(code, 60)
    today = hist[-1] if hist else None
    # 统计
    def _sum(rows, k, n):
        return sum(r[k] for r in rows[-n:]) if rows else 0
    streak = 0
    for r in reversed(hist):
        if r["dark_net"] > 0:
            streak += 1
        else:
            break
    return {
        "code": code,
        "name": (today or {}).get("name") or code,
        "hist": hist, "klines": kl,
        "date": (today or {}).get("real_date") or "",
        "dark_5": _sum(hist, "dark_net", 5), "dark_20": _sum(hist, "dark_net", 20),
        "bright_5": _sum(hist, "bright_net", 5), "bright_20": _sum(hist, "bright_net", 20),
        "dark_streak": streak,
        "active_avg": (sum(r["dark_active"] for r in hist[-20:]) / min(20, len(hist))) if hist else 0,
        "rank": (today or {}).get("rank"),
        "industry": (today or {}).get("industry", ""),
        "concept": (today or {}).get("concept", ""),
    }


# ================================================================
# 终端输出
# ================================================================

def print_report(d: dict) -> None:
    print("\n" + "=" * 64)
    print(f"暗盘资金扫描 · 数据日期 {d['date']} · 快照时间 {d['snap_time']}")
    print(f"全市场 {d['snap_count']} 只 | 暗盘合计 {yi(d['dark_sum'])} | "
          f"明盘合计 {yi(d['bright_sum'])} | 暗盘净流入家数占比 {d['pos_ratio'] * 100:.1f}%")
    print("=" * 64)

    def _line(i, r):
        extra = f" {r.get('industry', '')}" if r.get("industry") else ""
        print(f"{i:>2}. {r['name']:<8}({r['code']}) 暗盘{yi(r['dark_net']):>10} "
              f"明盘{yi(r['bright_net']):>10} 活跃{r['dark_active'] * 100:.0f}% "
              f"涨跌{r['pct_chg']:+.2f}%{extra}")

    print("\n【板块明暗盘对比 · 行业 TOP10(按明+暗合计)】")
    for i, r in enumerate(d["industry"][:10], 1):
        div = " ⇄背离" if (r["dark_net"] > 0) != (r["bright_net"] > 0) else ""
        print(f"{i:>2}. {r['name']:<10} 暗盘{yi(r['dark_net']):>10} 明盘{yi(r['bright_net']):>10}{div}")

    print("\n【个股明暗盘对比 TOP15(按合计)】")
    for i, r in enumerate(d["stock_total"][:15], 1):
        _line(i, r)

    print("\n【明暗背离 A · 暗进明出】(隐蔽通道吸筹, 公开通道流出)")
    if not d["div_a"]:
        print("  (今日无显著样本)")
    for i, r in enumerate(d["div_a"], 1):
        _line(i, r)

    print("\n【明暗背离 B · 明进暗出】(公开通道拉抬, 隐蔽通道撤退, 警惕)")
    if not d["div_b"]:
        print("  (今日无显著样本)")
    for i, r in enumerate(d["div_b"], 1):
        _line(i, r)

    print("\n口径: 暗盘资金=平台量化模型估算(非交易所官方), 单位已换算; 不构成投资建议。")


def print_stock(d: dict) -> None:
    print(f"\n=== {d['name']}({d['code']}) 暗盘深挖 · 数据日 {d['date']} ===")
    print(f"近5日 暗盘{yi(d['dark_5'])} 明盘{yi(d['bright_5'])} | "
          f"近20日 暗盘{yi(d['dark_20'])} 明盘{yi(d['bright_20'])}")
    print(f"暗盘连进 {d['dark_streak']} 天 | 近20日活跃度均值 {d['active_avg'] * 100:.1f}%"
          + (f" | 全市场暗盘排名 #{d['rank']}" if d.get("rank") else ""))
    if d.get("industry"):
        print(f"行业 {d['industry']} · 概念 {d.get('concept', '')}")
    print("最近 10 个交易日:")
    for r in (d["hist"] or [])[-10:]:
        bar = "█" * max(0, min(20, int(abs(r["dark_net"]) / max(1, abs(d["dark_5"])) * 20)))
        sign = "+" if r["dark_net"] > 0 else "-"
        print(f"  {r['real_date']}  暗盘{yi(r['dark_net']):>10}  明盘{yi(r['bright_net']):>10}  {sign}{bar}")


# ================================================================
# HTML 报告(深色单文件, #0d1117)
# ================================================================

def _table(rows: list[dict], money_keys: tuple[str, ...], extra_cols: tuple[tuple[str, str], ...] = ()) -> str:
    esc = html.escape
    head = "".join(f"<th>{esc(t)}</th>" for t, _ in extra_cols)
    body = []
    for i, r in enumerate(rows, 1):
        cells = [f"<td class='rk'>{i}</td>",
                 f"<td><b>{esc(r['name'])}</b><span class='code'>{esc(r['code'])}</span></td>"]
        for k in money_keys:
            v = r[k]
            cls = "pos" if v > 0 else ("neg" if v < 0 else "")
            cells.append(f"<td class='num {cls}'>{yi(v)}</td>")
        for _, fn in extra_cols:
            cells.append(f"<td class='num'>{fn(r)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    money_head = "".join(f"<th>{'暗盘净流入' if k == 'dark_net' else '明盘净流入'}</th>" for k in money_keys)
    return (f"<table><thead><tr><th>#</th><th>名称</th>{money_head}{head}</tr></thead>"
            f"<tbody>{''.join(body) or '<tr><td colspan=9 class=dim>今日无显著样本</td></tr>'}</tbody></table>")


def _pairbars(rows: list[dict], topn: int, name_key="name") -> str:
    """明暗双条对比(按合计排序): 暗盘红条/明盘绿条, 符号相反的高亮为背离。"""
    esc = html.escape
    rows = rows[:topn]
    if not rows:
        return "<div class='dim'>无数据</div>"
    mx = max(1e-9, max(max(abs(r["dark_net"]), abs(r["bright_net"])) for r in rows))
    out = []
    for i, r in enumerate(rows, 1):
        dw = abs(r["dark_net"]) / mx * 100
        bw = abs(r["bright_net"]) / mx * 100
        divergent = (r["dark_net"] > 0) != (r["bright_net"] > 0) and min(
            abs(r["dark_net"]), abs(r["bright_net"])) / mx > 0.03
        cls = " brow-div" if divergent else ""
        out.append(f"""<div class="brow{cls}">
  <span class="brk">{i}</span><span class="bname">{esc(str(r.get(name_key) or r.get('leader_name') or ''))}</span>
  <div class="bpair">
    <div class="blab">暗</div><div class="bbar"><div class="bfill {'bdneg' if r['dark_net'] < 0 else 'bdpos'}" style="width:{dw:.0f}%"></div></div><span class="bnum {'pos' if r['dark_net'] > 0 else 'neg'}">{yi(r['dark_net'])}</span>
    <div class="blab">明</div><div class="bbar"><div class="bfill {'bbneg' if r['bright_net'] < 0 else 'bbpos'}" style="width:{bw:.0f}%"></div></div><span class="bnum {'pos' if r['bright_net'] > 0 else 'neg'}">{yi(r['bright_net'])}</span>
  </div>
  <span class="bextra">{f"{r.get('inflow_count')}/{r.get('outflow_count')}家" if r.get('inflow_count') is not None else f"活跃{r.get('dark_active', 0) * 100:.0f}%"}</span>
</div>""")
    return "".join(out) + "<div class='dim' style='margin-top:6px'>琥珀底=明暗方向背离(两条通道唱反调)</div>"


def _svg_series(series: list[tuple[str, list[float]]], labels: list[str],
                colors: list[str], w: int = 980, h: int = 180, unit_fmt=yi) -> str:
    """纯 SVG 多线图(零依赖)。series=[(dates? 共享)…] 简化为等距点。"""
    if not series or not series[0] or not series[0][1]:
        return "<div class='dim'>无数据</div>"
    vals = [v for _, arr in series for v in arr if v is not None]
    if not vals:
        return "<div class='dim'>无数据</div>"
    lo, hi = min(vals + [0]), max(vals + [0])
    rng = (hi - lo) or 1e-9
    n = max(len(arr) for _, arr in series)
    pad_l, pad_b = 8, 18

    def _pt(arr, i):
        x = pad_l + i * (w - pad_l - 8) / max(1, n - 1)
        y = 8 + (hi - arr[i]) / rng * (h - 8 - pad_b)
        return f"{x:.1f},{y:.1f}"

    zero_y = 8 + (hi - 0) / rng * (h - 8 - pad_b)
    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart">',
             f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{w - 8}" y2="{zero_y:.1f}" stroke="#3a4552" stroke-dasharray="4 4"/>']
    for (lbl, arr), color in zip(series, colors):
        pts = " ".join(_pt(arr, i) for i in range(len(arr)))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round"/>')
        lx, ly = _pt(arr, len(arr) - 1).split(",")
        parts.append(f'<circle cx="{lx}" cy="{ly}" r="3.5" fill="{color}"/>')
        parts.append(f'<text x="{float(lx) - 4}" y="{float(ly) - 8}" fill="{color}" font-size="12" text-anchor="end">{html.escape(lbl)} {unit_fmt(arr[-1])}</text>')
    if labels:
        parts.append(f'<text x="{pad_l}" y="{h - 4}" fill="#5c636b" font-size="11">{html.escape(labels[0])}</text>')
        parts.append(f'<text x="{w - 8}" y="{h - 4}" fill="#5c636b" font-size="11" text-anchor="end">{html.escape(labels[-1])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_bars(vals: list[float], dates: list[str], color_pos="#e0455a", color_neg="#17b890",
              w: int = 980, h: int = 150) -> str:
    """纯 SVG 柱状图(正负双色)。"""
    if not vals:
        return "<div class='dim'>无数据</div>"
    mx = max(1e-9, max(abs(v) for v in vals))
    n = len(vals)
    bw = (w - 16) / n
    zero_y = h * 0.5 if (min(vals) < 0 < max(vals)) else (h - 20 if min(vals) >= 0 else 8)
    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart">',
             f'<line x1="8" y1="{zero_y}" x2="{w - 8}" y2="{zero_y}" stroke="#3a4552"/>']
    for i, v in enumerate(vals):
        bh = abs(v) / mx * (h * 0.42)
        x = 8 + i * bw
        y = zero_y - bh if v >= 0 else zero_y
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw * 0.7:.1f}" height="{bh:.1f}" fill="{color_pos if v >= 0 else color_neg}" rx="1.5"/>')
    if dates:
        parts.append(f'<text x="8" y="{h - 2}" fill="#5c636b" font-size="11">{html.escape(dates[0])}</text>')
        parts.append(f'<text x="{w - 8}" y="{h - 2}" fill="#5c636b" font-size="11" text-anchor="end">{html.escape(dates[-1])}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ================================================================
# 皮肤(2026-09-05 impeccable 改版: 模块分层 · 深色专业终端风)
# ================================================================
_SKIN = """
:root { --bg:#070b12; --card1:#151c26; --card2:#0f151d; --line:#232e3b; --line2:#2c3949;
        --head:#eef2f7; --body:#c3ccd7; --mut:#6d7987;
        --gold:#e3b341; --up:#f04a62; --down:#1ebd8c; }
* { margin:0; padding:0; box-sizing:border-box; }
body { background:radial-gradient(1100px 420px at 50% -140px, #18223366 0%, transparent 60%), var(--bg);
       color:var(--body); font:14px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
       -webkit-font-smoothing:antialiased; }
::selection { background:var(--gold); color:#14100a; }
.wrap { max-width:1120px; margin:0 auto; padding:28px 20px 64px; }
/* ── 报头 ── */
.mast { display:flex; align-items:flex-end; justify-content:space-between; gap:18px;
        padding-bottom:16px; border-bottom:1px solid var(--line); }
.mast h1 { font-size:26px; font-weight:800; color:var(--head); letter-spacing:1px; }
.mast h1 .tick { display:inline-block; width:10px; height:10px; border-radius:2px; background:var(--gold); margin-right:10px; }
.mast .meta { text-align:right; font-family:Consolas,monospace; font-size:12px; color:var(--mut); line-height:1.6; }
.mast .meta b { color:var(--gold); }
/* ── 氛围仪表卡 ── */
.gauge-card { background:linear-gradient(150deg,var(--card1),var(--card2)); border:1px solid var(--line);
              border-radius:14px; padding:18px 22px; margin-top:18px; box-shadow:0 1px 0 #ffffff08 inset, 0 14px 34px #00000055; }
.gauge-top { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:12px; }
.gauge-top .gt { font-size:12px; letter-spacing:2px; color:var(--mut); }
.gauge-top .gv { font-family:Consolas,monospace; font-size:15px; font-weight:700; }
.gauge { position:relative; height:10px; border-radius:6px;
         background:linear-gradient(90deg,#1ebd8c 0%, #2a3a34 38%, #2c2a33 62%, #f04a62 100%); }
.gauge .needle { position:absolute; top:-4px; width:4px; height:18px; border-radius:2px; background:#fff;
                 box-shadow:0 0 8px #ffffff88; }
.gauge-lab { display:flex; justify-content:space-between; font-size:11px; color:var(--mut); margin-top:7px; }
.moodline { margin-top:12px; font-size:13px; color:#e8c56a; }
/* ── 概览卡 ── */
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin-top:16px; }
.card { background:linear-gradient(150deg,var(--card1),var(--card2)); border:1px solid var(--line);
        border-radius:14px; padding:15px 17px; transition:transform .25s cubic-bezier(.16,1,.3,1), border-color .25s; }
.card:hover { transform:translateY(-2px); border-color:var(--line2); }
.card .label { color:var(--mut); font-size:11.5px; letter-spacing:1px; }
.card .val { font-size:24px; font-weight:800; font-family:Consolas,monospace; margin-top:5px; font-variant-numeric:tabular-nums; }
.card .sub2 { color:var(--mut); font-size:11px; margin-top:4px; }
/* ── 模块头(分层编号) ── */
.mod { display:flex; align-items:center; gap:12px; margin:38px 0 12px; }
.mod .mno { flex:none; width:30px; height:30px; border:1.5px solid var(--gold); border-radius:8px;
            color:var(--gold); font-family:Consolas,monospace; font-weight:800; font-size:14px;
            display:flex; align-items:center; justify-content:center; }
.mod h2 { font-size:17px; font-weight:800; color:var(--head); letter-spacing:.5px; }
.mod .hint { color:var(--mut); font-size:11.5px; margin-left:auto; text-align:right; }
.mod .rule { flex:1; height:1px; background:var(--line); }
.mod .tag { font-size:10.5px; color:var(--gold); border:1px solid #e3b34155; border-radius:6px; padding:2px 8px; margin-left:8px; }
/* 个股深挖页的区标题(无编号) */
h2 { font-size:15px; font-weight:800; color:var(--head); margin:28px 0 10px; letter-spacing:.5px; }
h2::before { content:""; width:8px; height:8px; background:var(--gold); border-radius:2px;
             display:inline-block; margin-right:9px; transform:translateY(-1px); }
/* ── 表格 ── */
table { width:100%; border-collapse:collapse; background:linear-gradient(150deg,var(--card1),var(--card2));
        border:1px solid var(--line); border-radius:14px; overflow:hidden; font-size:13px;
        box-shadow:0 1px 0 #ffffff06 inset; }
th { text-align:left; color:var(--mut); font-weight:600; font-size:11.5px; letter-spacing:1px;
     padding:10px 12px; border-bottom:1px solid var(--line); background:#0d1420; }
td { padding:8px 12px; border-bottom:1px solid #1a222d; }
tr:last-child td { border-bottom:none; }
tbody tr:hover { background:#ffffff05; }
td.num { font-family:Consolas,monospace; text-align:right; font-variant-numeric:tabular-nums; }
td .code { color:var(--mut); font-size:11px; margin-left:6px; }
td.rk { color:var(--mut); width:30px; font-family:Consolas,monospace; }
.pos { color:var(--up); } .neg { color:var(--down); } .dim { color:var(--mut); }
/* ── 明暗双条看板 ── */
.brow { display:flex; align-items:center; gap:12px; background:linear-gradient(150deg,var(--card1),var(--card2));
        border:1px solid var(--line); border-radius:12px; padding:10px 14px; margin-bottom:7px;
        transition:border-color .2s; }
.brow:hover { border-color:var(--line2); }
.brow-div { border-color:#e3b34177; box-shadow:0 0 0 1px #e3b34122, 0 0 18px #e3b34111; }
.brk { color:var(--mut); width:22px; font-family:Consolas,monospace; font-size:12px; }
.bname { width:158px; font-weight:700; color:var(--head); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.bpair { flex:1; display:grid; grid-template-columns:18px 1fr 82px; align-items:center; gap:4px 8px; }
.blab { color:var(--mut); font-size:10.5px; }
.bbar { background:#0a0f16; border-radius:5px; height:12px; overflow:hidden; }
.bfill { height:100%; border-radius:5px; }
.bdpos, .bbpos { background:linear-gradient(90deg,#f04a62cc,#f04a62); }
.bdneg, .bbneg { background:linear-gradient(90deg,#1ebd8ccc,#1ebd8c); }
.bnum { font-family:Consolas,monospace; font-size:12px; text-align:right; font-variant-numeric:tabular-nums; }
.bextra { color:var(--mut); font-size:11px; width:70px; text-align:right; font-family:Consolas,monospace; }
/* ── 背离双卡 ── */
.duo { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
@media (max-width:860px) { .duo { grid-template-columns:1fr; } }
.duo .dc { border-radius:14px; border:1px solid var(--line); overflow:hidden; }
.duo .dc-a { background:linear-gradient(160deg,#1c1a10,#10161e); }
.duo .dc-b { background:linear-gradient(160deg,#20131a,#10161e); }
.duo .dhead { padding:10px 14px; font-size:12.5px; font-weight:700; letter-spacing:.5px; }
.dc-a .dhead { color:#e8c56a; border-bottom:1px solid #e3b34133; }
.dc-b .dhead { color:#f08a9c; border-bottom:1px solid #f04a6233; }
.duo table { border:none; border-radius:0; background:transparent; box-shadow:none; }
.duo th { background:transparent; }
.warn { background:#241116; border:1px solid #f04a6240; color:#f0a5b2; border-radius:10px;
        padding:10px 14px; margin:12px 0; font-size:12.5px; }
.foot { margin-top:34px; background:linear-gradient(150deg,var(--card1),var(--card2));
        border:1px solid var(--line); border-radius:14px; padding:16px 20px;
        color:var(--mut); font-size:11.5px; line-height:1.9; }
.foot b { color:var(--gold); }
.chart { width:100%; background:linear-gradient(150deg,var(--card1),var(--card2));
         border:1px solid var(--line); border-radius:14px; }
"""


def _mod(no: str, title: str, hint: str = "", tag: str = "") -> str:
    esc = html.escape
    return (f'<div class="mod"><span class="mno">{esc(no)}</span><h2>{esc(title)}'
            f'{f"<span class=tag>{esc(tag)}</span>" if tag else ""}</h2>'
            f'<span class="rule"></span>{f"<span class=hint>{esc(hint)}</span>" if hint else ""}</div>')


def render_html(d: dict) -> str:
    esc = html.escape
    pct = lambda r: f"{r['pct_chg']:+.2f}%"  # noqa: E731
    ind_fn = lambda r: esc(r.get("industry", ""))  # noqa: E731
    act_fn = lambda r: f"{r['dark_active'] * 100:.0f}%"  # noqa: E731

    ratio = d["pos_ratio"] * 100
    mood_txt = "暗盘整体净流入为正, 偏多氛围" if d["dark_sum"] > 0 else "暗盘整体净流出, 偏谨慎氛围"
    needle = max(2, min(98, ratio))  # 家数占比当指针

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>暗盘资金扫描 · {esc(d['date'])}</title>
<style>{_SKIN}</style></head><body><div class="wrap">

<div class="mast">
  <h1><span class="tick"></span>暗盘资金扫描</h1>
  <div class="meta">数据日期 <b>{esc(d['date'])}</b> · 快照 {esc(d['snap_time'])}<br>生成于 {datetime.now():%Y-%m-%d %H:%M:%S}</div>
</div>
<div class="sub" style="margin-top:8px">看主力在公开通道和隐蔽通道是不是同向</div>

<div class="gauge-card">
  <div class="gauge-top"><span class="gt">市场暗盘氛围 · 净流入家数占比</span>
    <span class="gv" style="color:{'#f04a62' if d['dark_sum'] > 0 else '#1ebd8c'}">{yi(d['dark_sum'])} · {ratio:.1f}%</span></div>
  <div class="gauge"><div class="needle" style="left:calc({needle:.0f}% - 2px)"></div></div>
  <div class="gauge-lab"><span>净流出(谨慎)</span><span>净流入(偏多)</span></div>
  <div class="moodline">◆ {mood_txt} —— 背离榜是本报告核心: 两条通道方向相反的票, 往往比单纯的流入榜更有信息量。</div>
</div>

<div class="grid">
  <div class="card"><div class="label">全市场暗盘合计</div><div class="val" style="color:{'#f04a62' if d['dark_sum'] > 0 else '#1ebd8c'}">{yi(d['dark_sum'])}</div>
    <div class="sub2">覆盖 {d['snap_count']} 只(含北交所)</div></div>
  <div class="card"><div class="label">全市场明盘合计</div><div class="val" style="color:{'#f04a62' if d['bright_sum'] > 0 else '#1ebd8c'}">{yi(d['bright_sum'])}</div>
    <div class="sub2">公开通道资金</div></div>
  <div class="card"><div class="label">暗盘净流入家数占比</div><div class="val">{ratio:.1f}%</div>
    <div class="sub2">越高越多资金在隐蔽吸货</div></div>
  <div class="card"><div class="label">明暗背离样本</div><div class="val" style="font-size:20px">{len(d['div_a'])} / {len(d['div_b'])}</div>
    <div class="sub2">暗进明出 / 明进暗出</div></div>
</div>

{_mod('01', '板块明暗盘对比 · 行业', '按明+暗合计排序 · 琥珀光圈=两条通道唱反调')}
{_pairbars(d['industry'], 15)}
{_mod('02', '板块明暗盘对比 · 概念', '按明+暗合计排序 · 琥珀光圈=两条通道唱反调')}
{_pairbars(d['concept'], 15)}
{_mod('03', '个股明暗盘对比', '按明+暗合计排序')}
{_pairbars(d['stock_total'], 15)}

{_mod('04', '明暗背离双榜', '线索不是结论, 结合位置/题材/量能再看')}
<div class="duo">
  <div class="dc dc-a">
    <div class="dhead">A · 暗进明出 — 隐蔽通道吸筹, 公开通道流出</div>
    {_table(d['div_a'], ('dark_net', 'bright_net'), (('活跃', act_fn), ('涨幅', pct), ('行业', ind_fn)))}
  </div>
  <div class="dc dc-b">
    <div class="dhead">B · 明进暗出 — 公开通道拉抬, 隐蔽通道撤退</div>
    {_table(d['div_b'], ('bright_net', 'dark_net'), (('活跃', act_fn), ('涨幅', pct), ('行业', ind_fn)))}
  </div>
</div>
<div class="warn">B 榜(明进暗出)是"表面热闹、底下撤退"的经典形态 —— 出现在此榜且涨幅已大的票, 格外小心追高。</div>

{_mod('05', '暗盘净流入 TOP20', '个股榜 · 带活跃度/涨幅/行业')}
{_table(d['dark_top'], ('dark_net', 'bright_net'), (('活跃度', act_fn), ('涨幅', pct), ('行业', ind_fn)))}
{_mod('06', '明盘净流入 TOP10', '公开通道榜 · 带活跃度/涨幅/行业')}
{_table(d['bright_top'], ('bright_net', 'dark_net'), (('活跃度', act_fn), ('涨幅', pct), ('行业', ind_fn)))}

<div class="foot">
口径声明: 「暗盘资金」为东方财富平台量化模型的<b>估算口径</b>, 非交易所官方披露数据; 金额单位已换算(亿/万);
个股榜覆盖约 {d['total_stocks']} 只, 全市场快照覆盖 {d['snap_count']} 只(含北交所/沪B), 两个口径入选范围不同属正常。<br>
背离榜 A = 暗盘净流入&gt;0 且明盘净流出&lt;0(取暗盘前 15); 背离榜 B = 明盘净流入&gt;0 且暗盘净流出&lt;0(取明盘前 15);
对比看板按"明+暗合计"降序, 琥珀光圈行=明暗方向背离。<br>
本工具只做数据整理与呈现, 不输出买卖点, 不构成投资建议; 据此操作, 风险自负。<br>
个股深挖: python darkpool_scan.py --code 600519(日度明暗盘历史五联图)。
</div>
</div></body></html>"""


def render_stock_html(d: dict) -> str:
    """个股深挖报告: 明暗盘日度柱 + 价格线 + 成交量柱 四联。"""
    esc = html.escape
    hist = d["hist"]
    dates = [r["real_date"] for r in hist]
    dark_arr = [r["dark_net"] for r in hist]
    bright_arr = [r["bright_net"] for r in hist]
    act_arr = [r["dark_active"] * 100 for r in hist]
    kl = d["klines"]
    kdates = [k["date"] for k in kl]
    price_arr = [k["close"] for k in kl]
    vol_arr = [k["volume"] for k in kl]

    dark_svg = _svg_bars(dark_arr, dates)
    bright_svg = _svg_bars(bright_arr, dates)
    price_svg = _svg_series([("收盘价", price_arr)], kdates, ["#d4a017"], unit_fmt=lambda v: f"{v:.2f}")
    vol_svg = _svg_bars(vol_arr, kdates, color_pos="#4a7fb5", color_neg="#4a7fb5")
    act_svg = _svg_series([("暗盘活跃度%", act_arr)], dates, ["#b07fd6"], unit_fmt=lambda v: f"{v:.0f}%")

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>暗盘深挖 · {esc(d['name'])}({esc(d['code'])})</title>
<style>{_SKIN}</style></head><body><div class="wrap">
<div class="mast">
  <h1><span class="tick"></span>暗盘深挖 · {esc(d['name'])}</h1>
  <div class="meta">{esc(d['code'])} · 数据日 <b>{esc(d['date'])}</b><br>生成于 {datetime.now():%Y-%m-%d %H:%M:%S}</div>
</div>
<div class="sub" style="margin-top:8px">{esc(d.get('industry', ''))} · {esc(d.get('concept', ''))}</div>
<div class="grid">
  <div class="card"><div class="label">近 5 日暗盘净额</div><div class="val {'pos' if d['dark_5'] > 0 else 'neg'}">{yi(d['dark_5'])}</div>
    <div class="sub2">明盘 {yi(d['bright_5'])}</div></div>
  <div class="card"><div class="label">近 20 日暗盘净额</div><div class="val {'pos' if d['dark_20'] > 0 else 'neg'}">{yi(d['dark_20'])}</div>
    <div class="sub2">明盘 {yi(d['bright_20'])}</div></div>
  <div class="card"><div class="label">暗盘连进天数</div><div class="val">{d['dark_streak']} 天</div>
    <div class="sub2">连续净流入(暗盘)</div></div>
  <div class="card"><div class="label">近 20 日活跃度均值</div><div class="val">{d['active_avg'] * 100:.1f}%</div>
    <div class="sub2">{f"全市场暗盘排名 #{d['rank']}" if d.get('rank') else '活跃度=暗盘成交占比估算'}</div></div>
</div>
<h2>暗盘资金净流入 · 日度</h2>
{dark_svg}
<h2>明盘资金净流入 · 日度</h2>
{bright_svg}
<h2>暗盘活跃度 · 日度</h2>
{act_svg}
<h2>收盘价(新浪日K, 不复权)</h2>
{price_svg}
<h2>成交量</h2>
{vol_svg}
<div class="foot">
口径声明: 暗盘资金/活跃度为东方财富平台量化模型估算, 非交易所官方披露; 历史序列由 darktradeone 按日回看拼装(约 112 天边界),
价格与成交量来自新浪日 K(不复权, 短窗口示意)。本工具只做数据整理与呈现, 不构成投资建议; 据此操作, 风险自负。
</div>
</div></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description="暗盘资金扫描器 v2")
    ap.add_argument("--date", default="", help="交易日 yyyymmdd, 默认最新(可回看约 112 天)")
    ap.add_argument("--code", default="", help="个股深挖模式: 6 位代码, 如 600519")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    if args.code:
        code = "".join(ch for ch in args.code if ch.isdigit()).zfill(6)
        d = gather_stock(code)
        print_stock(d)
        out = OUT_DIR / f"暗盘深挖_{d['name']}_{d['date'] or 'latest'}.html"
        out.write_text(render_stock_html(d), encoding="utf-8")
        log(f"HTML 报告已写入: {out}")
        if not args.no_open:
            webbrowser.open(out.as_uri())
        return

    d = gather(args.date)
    print_report(d)
    out = OUT_DIR / f"暗盘资金_{d['date']}.html"
    out.write_text(render_html(d), encoding="utf-8")
    log(f"HTML 报告已写入: {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
