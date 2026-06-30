#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit hook: 按当前输入动态召回相关记忆并注入上下文。

取代"每次会话全量加载 MEMORY.md": 只把与这句话真正相关的几条放进上下文,
记忆涨到几千条, 单轮记忆 token 仍然恒定。

- 召回范围: 当前项目 active + 全局 global
- 归档区强命中 → 自动"复活"回 active (修正误归档)
- 命中记一次访问(支撑"久未命中→建议归档")
- 任何异常都静默 exit 0, 绝不阻塞用户输入

接入: 见 hooks/settings-snippet.json
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 复活判定用相对阈值: 归档项分数达到本次最高分的这个比例即复活
# (BM25 绝对分随语料波动, 相对阈值更稳健 —— "它就是当前最相关的之一就该回来")
RESURRECT_RATIO = 0.5
TOP_K = 6


def project_slug_from_cwd(cwd: str) -> str:
    """把工作目录还原成 Claude 的项目目录编码, 如 D:\\work\\foo → D--work-foo。"""
    return re.sub(r"[:\\/.]", "-", cwd)


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return  # 读不到输入就放行

    prompt = (data.get("prompt") or "").strip()
    cwd = data.get("cwd") or os.getcwd()
    if not prompt:
        return

    try:
        from memmgr import index, retrieval, lifecycle, config as C

        con = index.connect()
        project = project_slug_from_cwd(cwd)

        # 同时搜 active + archived, 让相关的归档记忆有机会复活
        hits = retrieval.search(
            con, prompt, top_k=TOP_K + 4,
            projects=[project, C.GLOBAL_PROJECT_ID],
            tiers=[C.STATUS_ACTIVE, C.STATUS_ARCHIVED],
        )
        if not hits:
            return

        best = max(h.score for h in hits)
        resurrect_floor = max(best * RESURRECT_RATIO, 0.2)
        chosen = []
        for h in hits:
            r = h.row
            if r["tier"] == C.STATUS_ARCHIVED:
                if h.score >= resurrect_floor:
                    try:
                        new_path = lifecycle.restore(
                            con, r["path"], reason="召回强命中, 自动复活")
                        # 路径已变, 取复活后的行(记访问/展示用新路径)
                        r = index.get(con, new_path) or r
                    except Exception:
                        pass
                    chosen.append(r)
            else:
                chosen.append(r)
            if len(chosen) >= TOP_K:
                break

        if not chosen:
            return

        # 记访问
        try:
            index.bump_access(con, [r["path"] for r in chosen])
        except Exception:
            pass

        lines = ["# 相关记忆 (memmgr 动态召回)\n"]
        for r in chosen:
            scope = "🌐" if r["scope"] == "global" else "📁"
            lines.append(f"- {scope} **{r['name']}**: {r['description']}")
            body = (r["body"] or "").strip()
            if body:
                snippet = body[:600]
                lines.append(f"  {snippet}")
        context = "\n".join(lines)

        out = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
        print(json.dumps(out, ensure_ascii=False))
    except Exception:
        if os.environ.get("MEMMGR_DEBUG"):
            import traceback
            traceback.print_exc(file=sys.stderr)
        return  # 任何失败都静默放行


if __name__ == "__main__":
    main()
