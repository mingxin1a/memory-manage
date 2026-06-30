#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop hook: 会话结束时的记忆维护。

可靠且默认开启的部分:
  1. 重建索引 —— 收录 Claude 本次会话新写的记忆
  2. git 快照 —— 每次会话结束都留一个可回溯的还原点
  3. 去重闸 —— 把新产生的疑似重复写进待审队列(不自动删, 交面板/人确认)

可选(默认关, 需 ANTHROPIC_API_KEY 且设 MEMMGR_AUTO_EXTRACT=1):
  4. LLM 抽取 —— 从本次对话抽候选记忆, 过闸后写入 pending 区待你确认

始终 exit 0, 不阻塞。
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    try:
        from memmgr import index, gitbackup, config as C

        con = index.connect()

        # 1) 增量同步索引(收录本次新写/改动的记忆, 比全量重建快得多)
        index.sync(con)

        # 2) git 快照
        try:
            gitbackup.snapshot_commit("session end snapshot")
        except Exception:
            pass

        # 注: 全量去重(O(n²))不放在会话结束 hook 里, 改由面板/CLI 按需运行,
        #     避免拖慢会话收尾。

        # 4) 可选 LLM 抽取
        if os.environ.get("MEMMGR_AUTO_EXTRACT") == "1":
            try:
                _llm_extract(data, C)
            except Exception:
                pass

    except Exception:
        pass
    # Stop hook 无需输出


def _llm_extract(data: dict, C):
    """可选: 用 Anthropic API 从本次对话抽取候选记忆, 写入 pending 区待人工确认。

    需要: pip install anthropic, 且环境变量 ANTHROPIC_API_KEY。
    抽取结果不直接进 active, 而是落到 memory-manager/pending/, 由你在面板确认后采纳,
    这就是我们设计的"自动抽取 + 过质量闸"。
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return
    transcript_path = data.get("transcript_path")
    if not transcript_path or not Path(transcript_path).exists():
        return

    # 读取最近的对话文本(transcript 为 jsonl)
    lines = Path(transcript_path).read_text(encoding="utf-8", errors="replace").splitlines()
    convo = []
    for ln in lines[-80:]:
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        msg = ev.get("message") or {}
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, list):
            text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        else:
            text = str(content or "")
        if role and text.strip():
            convo.append(f"{role}: {text[:1500]}")
    if not convo:
        return

    import anthropic
    client = anthropic.Anthropic()
    prompt = (
        "下面是一段编程助手与用户的对话。请抽取其中值得长期记住的事实/偏好/项目约束, "
        "每条尽量精炼。只输出 JSON 数组, 每项 {name, description, type, body}, "
        "type ∈ user|feedback|project|reference。无可记则输出 []。\n\n"
        + "\n".join(convo)
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        return
    try:
        items = json.loads(text[start:end + 1])
    except Exception:
        return

    pending = C.MANAGER_DIR / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        fname = pending / f"{name}.md"
        body = it.get("body", "")
        meta = (f"---\nname: {name}\ndescription: {it.get('description','')}\n"
                f"metadata:\n  node_type: memory\n  type: {it.get('type','reference')}\n"
                f"  status: pending\n---\n\n{body}\n")
        fname.write_text(meta, encoding="utf-8")


if __name__ == "__main__":
    main()
