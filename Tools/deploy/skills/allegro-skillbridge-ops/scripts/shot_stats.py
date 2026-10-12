# -*- coding: utf-8 -*-
"""对 allegro_shot.png 做**像素级数值取证**（不依赖肉眼看图）。

判据：
  - 窗口区域是不是"活画面"（多色、非均匀、非全黑）
  - 画布特征色是否存在（Allegro 画布底色近黑 / 焊盘绿 / 丝印黄 / 白字）
用法：
  .venv/Scripts/python.exe shot_stats.py [json路径] [png路径]
更新：2026-09-30
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageStat

JSON_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"C:\Users\ds245\WorkBuddy\Layout\.workbuddy\cache\scratch\allegro_window.json")
PNG_PATH = Path(sys.argv[2]) if len(sys.argv) > 2 else None


def hue_buckets(colors, palette):
    """colors: [(count, palette_index), ...]；palette: q.getpalette() 平铺列表"""
    total = sum(c for c, _ in colors) or 1
    out = {"near_black": 0, "green": 0, "yellow": 0, "white": 0}
    for cnt, idx in colors:
        r, g, b = palette[idx * 3: idx * 3 + 3]
        if max(r, g, b) < 40:
            out["near_black"] += cnt
        if g > r + 30 and g > b + 30:
            out["green"] += cnt
        if r > 150 and g > 150 and b < min(r, g) - 40:
            out["yellow"] += cnt
        if r > 200 and g > 200 and b > 200:
            out["white"] += cnt
    return {k: round(v / total, 4) for k, v in out.items()}


def analyze(img: Image.Image, label: str) -> dict:
    im = img.convert("RGB")
    st = ImageStat.Stat(im)
    q = im.convert("P", palette=Image.ADAPTIVE, colors=256)
    palette = q.getpalette() or []
    colors = q.getcolors(256) or []
    colors.sort(reverse=True)
    mean = [round(v, 1) for v in st.mean]
    res = {
        "label": label,
        "size": im.size,
        "mean_rgb": mean,
        "stddev": [round(v, 1) for v in st.stddev],
        "unique_q_colors": len(colors),
        "top5": [[c, tuple(palette[i * 3: i * 3 + 3])] for c, i in colors[:5]],
        "hue": hue_buckets(colors, palette),
    }
    h = res["hue"]
    res["looks_live"] = (
        res["unique_q_colors"] >= 20
        and max(res["stddev"]) > 20
        and h["near_black"] < 0.97
    )
    res["has_canvas_signature"] = (
        h["green"] > 0.0005 or h["yellow"] > 0.0005
    )
    return res


def main() -> int:
    meta = json.loads(JSON_PATH.read_text(encoding="utf-8-sig"))
    shot = PNG_PATH or Path(meta["shot"])
    vs = meta["virtualScreen"]
    img = Image.open(shot)
    print(f"shot = {shot}")
    print(f"virtual screen = ({vs['x']},{vs['y']}) {vs['w']}x{vs['h']}   shot size = {img.size}")
    print()

    # ---- 整幅 ----
    full = analyze(img, "VirtualScreen(full)")
    print(f"[whole] size={full['size']} mean={full['mean_rgb']} std={full['stddev']} "
          f"uniq={full['unique_q_colors']} live={full['looks_live']} hue={full['hue']}")

    # ---- 每块屏 ----
    for s in meta["screens"]:
        box = (s["x"] - vs["x"], s["y"] - vs["y"],
               s["x"] - vs["x"] + s["w"], s["y"] - vs["y"] + s["h"])
        r = analyze(img.crop(box), f"{s['device']}{' (primary)' if s['primary'] else ''}")
        print(f"[screen] {r['label']:<22} mean={r['mean_rgb']} uniq={r['unique_q_colors']} "
              f"live={r['looks_live']} canvas_sig={r['has_canvas_signature']} hue={r['hue']}")

    # ---- 每个 Allegro 窗口（rect 原点换算 + 裁到图像范围）----
    for w in meta["windows"]:
        ix, iy = w["left"] - vs["x"], w["top"] - vs["y"]
        box = (max(0, ix), max(0, iy),
               min(img.width, ix + w["width"]), min(img.height, iy + w["height"]))
        r = analyze(img.crop(box), f"pid{w['pid']} {w['onScreen']}")
        print()
        print(f"[window] pid={w['pid']} title={w['title']!r}")
        print(f"         rect=L{w['left']} T{w['top']} R{w['right']} B{w['bottom']} "
              f"({w['width']}x{w['height']})  on={w['onScreen']}  fg={w['isForeground']} "
              f"min={w['minimized']} vis={w['visible']} cloaked={w['dwmCloaked']}")
        print(f"         image-box={box}  mean={r['mean_rgb']} std={r['stddev']} "
              f"uniq={r['unique_q_colors']}")
        print(f"         hue={r['hue']}")
        print(f"         top5={r['top5']}")
        print(f"         live={r['looks_live']}  canvas_signature(绿/黄)={r['has_canvas_signature']}")
        print()
        if r["looks_live"] and r["has_canvas_signature"]:
            print("VERDICT: Allegro 画布**已渲染**且可被截取（数值判据通过）")
        elif r["looks_live"]:
            print("VERDICT: 窗口区域有内容，但未见画布特征色（可能被子窗口/对话框占满）")
        else:
            print("VERDICT: 窗口区域近乎空白/纯色 —— 截取失败或画布未刷新")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
