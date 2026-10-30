# -*- coding: utf-8 -*-
"""形态标签标注器: 在报告的个股表格中, 为每只股票标注 skill 框架定义的"积极/警示形态"标签。

标签 = 数据形态名, 不是"利好"判断, 不是买入建议。
  【持续吸筹】 暗盘连进>=3天 且 明暗双正 且 近5日暗盘净额>0
  【暗进明出】 当日暗盘大额净流入 且 明盘净流出 (skill 背离A)
  【明进暗出】 当日明盘净流入 且 暗盘净流出 (skill 背离B, 警示)
  【单日脉冲】 当日暗盘净流入但连进仅1-2天 (持续性不足)
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import darkpool_scan as d  # noqa: E402

OUT = Path(os.environ.get("REPORT_PATH", "/workspace/darkpool_daily.html"))

TAG_STYLE = {
    "持续吸筹": "background:linear-gradient(90deg,#ffd75e,#ffb020);color:#1a1205;border:1px solid #ffd75e;font-weight:800;box-shadow:0 0 10px #ffb02088",
    "暗进明出": "background:#e3b34122;color:#e8c56a;border:1px solid #e3b34166",
    "单日脉冲": "background:#6d798722;color:#9aa5b1;border:1px solid #6d798755",
    "明进暗出": "background:#f04a6222;color:#f08a9c;border:1px solid #f04a6255",
}
# 持续吸筹: 金色渐变高亮 + 行底色 + 名称加色
SPECIAL = "持续吸筹"


def tag_html(name):
    st = TAG_STYLE.get(name, TAG_STYLE["单日脉冲"])
    if name == SPECIAL:
        return (f'<span data-tag="1" style="{st};font-size:11px;padding:2px 9px;border-radius:6px;'
                f'margin-left:6px;white-space:nowrap;letter-spacing:.5px">★ {name}</span>')
    return (f'<span data-tag="1" style="{st};font-size:10.5px;padding:1px 6px;border-radius:5px;'
            f'margin-left:6px;white-space:nowrap">{name}</span>')


def classify(code, snap_row, hist=None):
    """返回 (标签, 说明)。hist 为 None 时只做当日判断。"""
    dark, bright = snap_row["dark_net"], snap_row["bright_net"]
    if dark > 0 and bright < 0:
        return "暗进明出", f"当日暗{ d.yi(dark) }/明{ d.yi(bright) }，隐蔽吸筹形态"
    if dark < 0 and bright > 0:
        return "明进暗出", f"当日明{ d.yi(bright) }/暗{ d.yi(dark) }，边拉边出形态"
    if hist:
        streak = 0
        for x in reversed(hist):
            if x["dark_net"] > 0:
                streak += 1
            else:
                break
        d5 = sum(x["dark_net"] for x in hist[-5:])
        b5 = sum(x["bright_net"] for x in hist[-5:])
        if streak >= 3 and d5 > 0 and b5 > 0:
            return "持续吸筹", f"暗盘连进{streak}天，近5日暗{d.yi(d5)}/明{d.yi(b5)}"
        if dark > 0 and bright > 0:
            return "单日脉冲", f"当日双正但连进仅{streak}天，持续性待观察"
    if dark > 0 and bright > 0:
        return "单日脉冲", "当日双正，持续性待观察"
    return None, ""


def main():
    html = OUT.read_text(encoding="utf-8")
    # 清理旧标签(仅表格内10.5px小标签, 不触碰观察清单卡片的★大标签)与旧行高亮
    html = re.sub(r'<span style="background:#[0-9a-f]{6}22;color:#[0-9a-f]{6};border:1px solid #[0-9a-f]{6}55;font-size:10\.5px;padding:1px 6px[^"]*">(?:暗进明出|单日脉冲|明进暗出)</span>', "", html)
    # 表内标签统一带 data-tag="1", 反复运行不会叠加; 观察清单卡片的★标签不带此属性, 不受影响
    html = re.sub(r'<span data-tag="1"[^>]*>(?:★ )?(?:持续吸筹|暗进明出|单日脉冲|明进暗出)</span>', "", html)
    html = re.sub(r'<tr style="background:linear-gradient\(90deg,#ffb0201f[^"]*">', "<tr>", html)

    _, snap = d.fetch_full_snapshot("")
    snap_map = {r["code"]: r for r in snap}
    hist_cache = {}

    def hist_of(code):
        if code not in hist_cache:
            try:
                hist_cache[code] = d.fetch_stock_history(code, 8)
            except Exception:
                hist_cache[code] = []
        return hist_cache[code]

    # 在表格行中定位 <b>名称</b><span class='code'>代码</span>, 在其后插入标签
    pat = re.compile(r"<b>([^<]+)</b><span class='code'>(\d{6})</span>")
    done, count = set(), 0

    def repl(m):
        nonlocal count
        name, code = m.group(1), m.group(2)
        if code in done or code not in snap_map:
            return m.group(0)
        label, why = classify(code, snap_map[code], hist_of(code))
        if not label:
            return m.group(0)
        done.add(code)
        count += 1
        return f"{m.group(0)}{tag_html(label)}"

    html = pat.sub(repl, html)

    # 整行高亮: 给含 ★持续吸筹 的 <tr> 加金色底纹描边
    def row_hl(m):
        row = m.group(0)
        if "★ 持续吸筹" not in row:
            return row
        style = ("background:linear-gradient(90deg,#ffb0201f,#ffb02008) !important;"
                 "box-shadow:inset 3px 0 0 #ffb020, inset 0 0 0 1px #ffb02055;")
        if "<tr " in row:
            return re.sub(r"<tr([^>]*)>", lambda x: f'<tr{x.group(1)} style="{style}">', row, count=1)
        return row.replace("<tr>", f'<tr style="{style}">', 1)

    html = re.sub(r"<tr[^>]*>.*?</tr>", row_hl, html, flags=re.S)

    # 图例说明
    legend = """<div class="warn" style="border-color:#e3b34140;color:#e8c56a">
◆ <b>形态标签说明</b>（标签是数据形态名，不是"利好/利空"判断，不是买入建议）：
<span style="color:#2ee0a8">持续吸筹</span>=暗盘连进≥3天且双通道同向净流入；
<span style="color:#e8c56a">暗进明出</span>=隐蔽通道吸筹、公开通道流出（背离A）；
<span style="color:#9aa5b1">单日脉冲</span>=仅当日双正、持续性不足；
<span style="color:#f08a9c">明进暗出</span>=公开通道拉抬、隐蔽通道撤退（背离B，需警惕）。
暗盘为平台估算口径，标签仅描述资金形态，不预示涨跌。</div>"""
    if "形态标签说明" not in html:
        html = html.replace('<div class="mod"><span class="mno">00</span>', legend + '<div class="mod"><span class="mno">00</span>', 1)

    OUT.write_text(html, encoding="utf-8")
    print(f"已标注 {count} 只个股形态标签")


if __name__ == "__main__":
    main()
