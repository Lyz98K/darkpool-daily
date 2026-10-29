# -*- coding: utf-8 -*-
"""次日观察清单生成器: 基于暗盘资金扫描结果, 生成"观察候选+数据理由+参考观察位"板块,
注入当日暗盘资金报告 HTML (在 </div></body></html> 前插入)。

定位: 数据呈现与研究参考, 不输出买卖指令/目标价。
"""
import os
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL))
import darkpool_scan as d  # noqa: E402


def yi(v):
    return d.yi(v)


def pick_candidates(snap, klines_of, max_n=6):
    """筛选规则(全部为数据条件, 非推荐):
    1. 暗盘净流入>3000万
    2. 暗盘连进>=3天(持续性, 剔除单日脉冲)
    3. 明暗双正(两通道同向, 排除背离噪声)
    4. 当日涨幅<8%(排除已涨停/高位追不进)
    5. 近5日暗盘净额>0
    按 暗盘连进天数*近5日暗盘净额 综合排序。
    """
    snap_dt = {}
    for r in snap:
        code = r["code"] or ""
        name = r["name"] or ""
        # 排除科创板(688/689开头)与ST股
        if code.startswith(("688", "689")):
            continue
        if "ST" in name.upper() or "退" in name:
            continue
        if r["dark_net"] > 3e7 and r["bright_net"] > 0 and r["pct_chg"] < 8:
            snap_dt[code] = r
    # 先按当日暗盘净额截断到前25只, 再逐只拉历史核验(控制耗时)
    pool = sorted(snap_dt.values(), key=lambda r: -r["dark_net"])[:25]
    cands = []
    for r in pool:
        code = r["code"]
        try:
            h = d.fetch_stock_history(code, 22)
        except Exception:
            continue
        time.sleep(0.2)
        if len(h) < 6:
            continue
        streak = 0
        for x in reversed(h):
            if x["dark_net"] > 0:
                streak += 1
            else:
                break
        d5 = sum(x["dark_net"] for x in h[-5:])
        b5 = sum(x["bright_net"] for x in h[-5:])
        if streak < 3 or d5 <= 0 or b5 <= 0:
            continue
        kl = klines_of(code)
        ma5 = sum(k["close"] for k in kl[-5:]) / 5 if len(kl) >= 5 else None
        ma10 = sum(k["close"] for k in kl[-10:]) / 10 if len(kl) >= 10 else None
        cands.append({
            **r, "streak": streak, "d5": d5, "b5": b5,
            "ma5": ma5, "ma10": ma10, "close": r["price"],
            "score": streak * (d5 / 1e8),
        })
        if len(cands) >= 40:
            break
    cands.sort(key=lambda x: -x["score"])
    return cands[:max_n]


def kl_fetcher():
    cache = {}

    def get(code):
        if code not in cache:
            cache[code] = d.sina_klines(code, 30)
        return cache[code]
    return get


def render_section(cands, date_str):
    cards = []
    for i, c in enumerate(cands, 1):
        ma5s = f"{c['ma5']:.2f}" if c["ma5"] else "—"
        ma10s = f"{c['ma10']:.2f}" if c["ma10"] else "—"
        ref = f"回踩 {ma10s}(10日线) 附近观察承接" if c["ma10"] else "暂无均线参考"
        cards.append(f"""
<div class="brow" style="display:block;background:linear-gradient(90deg,#ffb0201f,#151c26);border:1px solid #ffb02077;box-shadow:inset 3px 0 0 #ffb020, 0 0 18px #ffb0201a">
  <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px;flex-wrap:wrap">
    <span style="background:linear-gradient(90deg,#ffd75e,#ffb020);color:#1a1205;font-weight:800;font-size:11px;padding:2px 9px;border-radius:6px;letter-spacing:.5px">★ 持续吸筹</span>
    <b style="color:#eef2f7;font-size:15px">{i}. {c['name']}({c['code']})</b>
    <span style="color:#6d7987;font-size:12px">{c.get('industry') or '—'} · 收盘 {c['close']:.2f} · 今日 {c['pct_chg']:+.2f}%</span>
  </div>
  <div style="font-size:12.5px;line-height:1.9;color:#c3ccd7">
    <b style="color:#e3b341">数据理由:</b> 暗盘连进 <b style="color:#f04a62">{c['streak']}</b> 天 ·
    近5日暗盘净额 <b style="color:#f04a62">{yi(c['d5'])}</b> / 明盘 {yi(c['b5'])} ·
    今日暗盘 {yi(c['dark_net'])} 活跃 {c['dark_active']*100:.0f}%<br>
    <b style="color:#e3b341">观察参考位(数据位,非买入指令):</b> 5日线 {ma5s} / 10日线 {ma10s} →
    {ref}; 若放量跌破10日线则观察逻辑失效<br>
    <b style="color:#e3b341">失效条件:</b> 暗盘转为净流出 ≥2 天, 或明暗双撤
  </div>
</div>""")
    return f"""
<div class="mod"><span class="mno">00</span><h2>次日观察清单(自动生成)</h2>
<span class="tag">★ 持续吸筹形态</span></div>
<div class="warn" style="border-color:#e3b34140;color:#e8c56a">
◆ 本板块为<b>数据筛选结果</b>：列表中的个股全部满足「<b>暗盘连进≥3天 + 明暗双正 + 近5日暗盘净额&gt;0 + 当日涨幅&lt;8%</b>」，即报告中金色高亮的 <b>★持续吸筹</b> 形态。
"观察参考位"为均线数据位，用于设定<b>观察触发条件</b>，不是买入指令、不是目标价。
本页不构成投资建议；暗盘资金为平台估算口径；入选标的可能已是强势股，追高风险自负。</div>
{''.join(cards) or "<div class='dim'>今日无满足筛选条件的样本</div>"}
"""


def main():
    out_html = Path(os.environ.get("REPORT_PATH", "/workspace/darkpool_daily.html"))
    if not out_html.exists():
        print("报告文件不存在, 退出")
        sys.exit(1)
    date, snap = d.fetch_full_snapshot("")
    getkl = kl_fetcher()
    cands = pick_candidates(snap, getkl)
    # 快照不带名称/行业, 用个股接口补全
    for c in cands:
        try:
            one = d.fetch_stock_one(c["code"], "")
            if one:
                c["name"] = one["name"] or c["name"]
                c["industry"] = one.get("industry") or c.get("industry", "")
                if one.get("price"):
                    c["close"], c["pct_chg"] = one["price"], one["pct_chg"]
        except Exception:
            pass
        time.sleep(0.2)
    # 名称补全后二次过滤 ST/退市风险股
    cands = [c for c in cands
             if "ST" not in (c.get("name") or "").upper() and "退" not in (c.get("name") or "")]
    html = out_html.read_text(encoding="utf-8")
    # 防重复注入: 循环清除所有已存在的观察清单板块(兼容历史编号00/07)
    for old_no in ("00", "07"):
        while True:
            marker = f'<div class="mod"><span class="mno">{old_no}</span><h2>次日观察清单'
            start = html.find(marker)
            if start == -1:
                break
            end = html.find('<div class="mod"><span class="mno">01</span>', start)
            if end == -1:
                end = html.find('<div class="foot"', start)
            if end == -1:
                end = html.find("</div></body></html>", start)
            html = html[:start] + html[end:]
    section = render_section(cands, date)
    # 注入到模块01之前(置于顶端), 若找不到01则退回页尾
    anchor = '<div class="mod"><span class="mno">01</span>'
    if anchor in html:
        html = html.replace(anchor, section + anchor, 1)
    else:
        html = html.replace("</div></body></html>", section + "</div></body></html>")
    out_html.write_text(html, encoding="utf-8")
    print(f"观察清单已注入 {len(cands)} 只候选:")
    for c in cands:
        print(f"  {c['name']}({c['code']}) 连进{c['streak']}天 近5日暗盘{yi(c['d5'])} 5日线{c['ma5'] and round(c['ma5'],2)} 10日线{c['ma10'] and round(c['ma10'],2)}")


if __name__ == "__main__":
    main()
