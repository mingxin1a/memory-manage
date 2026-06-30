# -*- coding: utf-8 -*-
"""`memmgr status` —— glance 式终端健康仪表盘。

借鉴 btop/abtop 的"一眼看全局", 但只读、不实时刷新(记忆是静态数据),
聚焦本程序的设定: 三层数量 / 锁定 / 久未活跃 / 待办 / 重复 / 按项目分布。
零额外依赖: 纯 ANSI, Windows 下尝试开启 VT。
"""

from __future__ import annotations

import os
import sys

from . import config as C
from . import index, lifecycle


# ---- 颜色 ------------------------------------------------------------------
class _Ansi:
    def __init__(self, on: bool):
        self.on = on

    def __call__(self, code: str, text) -> str:
        if not self.on:
            return str(text)
        return f"\033[{code}m{text}\033[0m"


def _enable_vt_windows() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def _bar(n: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return ""
    filled = round(width * n / total)
    return "█" * filled + "·" * (width - filled)


# ---- 数据 ------------------------------------------------------------------

def status_data(con, include_dupes: bool = True) -> dict:
    """收集 status 所需数据(只读)。dupes 较贵(O(n²)), 可关。"""
    s = index.stats(con)
    stale = lifecycle.stale_candidates(con)
    todos = [r for r in index.all_rows(con, tier=C.STATUS_ACTIVE)
             if r["nature"] == C.NAT_TODO]
    data = {
        "total": s["total"],
        "by_tier": s["by_tier"],
        "pinned": s["pinned"],
        "by_project": s["by_project"],
        "by_type": s["by_type"],
        "by_nature": s.get("by_nature", {}),
        "by_volatility": s.get("by_volatility", {}),
        "by_scope": s["by_scope"],
        "stale": len(stale),
        "todos": len(todos),
        "dupes": None,
    }
    if include_dupes:
        data["dupes"] = len(lifecycle.duplicate_pairs(con, max_pairs=500))
    return data


# ---- 渲染 ------------------------------------------------------------------

def render(con, color: bool = True, include_dupes: bool = True) -> str:
    d = status_data(con, include_dupes=include_dupes)
    c = _Ansi(color)
    out: list[str] = []

    bt = d["by_tier"]
    active = bt.get(C.STATUS_ACTIVE, 0)
    out.append(c("1;36", "🧠 memmgr — Claude 记忆健康仪表盘"))
    out.append("─" * 52)
    out.append(
        f"  总数 {c('1', d['total']):>4}   "
        f"{c('32','● active')} {active}   "
        f"{c('33','◐ archived')} {bt.get(C.STATUS_ARCHIVED,0)}   "
        f"{c('90','✕ trash')} {bt.get(C.STATUS_TRASH,0)}   "
        f"{c('36','🔒 pinned')} {d['pinned']}"
    )

    # 健康度告警(数值大用红/黄)
    def warn(label, n, hi):
        col = "31" if n >= hi else ("33" if n > 0 else "32")
        return f"{label} {c(col, n)}"
    dupes = d["dupes"]
    dupes_s = warn("👯 重复", dupes, 5) if dupes is not None else "👯 重复 —"
    out.append("")
    out.append("  健康度: " + "   ".join([
        warn("⏳ 久未活跃", d["stale"], 20),
        warn("📝 待办", d["todos"], 30),
        dupes_s,
    ]))

    # 按项目分布(条形)
    bp = d["by_project"]
    if bp:
        out.append("")
        out.append(c("1", "  按项目 (active):"))
        mx = max(bp.values())
        for proj, n in list(bp.items())[:12]:
            label = C.decode_project_id(proj)
            out.append(f"    {n:>4} {c('36', _bar(n, mx))} {label}")

    # 维度分布
    def kv(m):
        return "  ".join(f"{k}:{v}" for k, v in m.items()) or "—"
    out.append("")
    out.append(c("1", "  维度:"))
    out.append(f"    类型     {kv(d['by_type'])}")
    out.append(f"    性质     {kv(d['by_nature'])}")
    out.append(f"    波动性   {kv(d['by_volatility'])}")
    out.append(f"    作用域   {kv(d['by_scope'])}")

    # 提示
    out.append("")
    hints = []
    if d["stale"]:
        hints.append("`memmgr stale` 看建议归档")
    if d["todos"]:
        hints.append("`memmgr todos` 看待办")
    if dupes:
        hints.append("`memmgr panel` 去重")
    if hints:
        out.append(c("90", "  → " + " · ".join(hints)))
    out.append(c("90", "  管理界面: memmgr panel"))
    return "\n".join(out)


def run(con, *, as_json: bool = False, watch: int = 0, full: bool = True) -> None:
    """status 命令入口。watch>0 则每 watch 秒刷新。"""
    if as_json:
        import json
        print(json.dumps(status_data(con, include_dupes=full), ensure_ascii=False, indent=2))
        return

    color = sys.stdout.isatty()
    _enable_vt_windows()

    if watch <= 0:
        print(render(con, color=color, include_dupes=full))
        return

    # --watch: 刷新; watch 模式跳过昂贵的 dupes 以保持流畅
    import time
    try:
        while True:
            con2 = index.connect()
            print("\033[2J\033[H", end="")  # 清屏
            print(render(con2, color=color, include_dupes=False))
            print(_Ansi(color)("90", f"\n  (每 {watch}s 刷新, Ctrl-C 退出)"))
            con2.close()
            time.sleep(watch)
    except KeyboardInterrupt:
        print()
