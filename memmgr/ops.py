"""操作日志 + 撤销 (undo)。

每一次会改动记忆文件的操作都记成一条 JSON(append-only), 内含每个被改文件的
完整 before/after 快照, 因此任何操作都能逆向还原。这是安全网的核心: 即使算法
判断错了, 也能一键回退。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as C


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Change:
    """单个文件的一次变更。path_before/after 为 None 表示创建/删除。"""

    def __init__(
        self,
        path_before: str | None,
        path_after: str | None,
        content_before: str | None,
        content_after: str | None,
        tier_before: str | None = None,
        tier_after: str | None = None,
        project: str | None = None,
    ):
        self.path_before = path_before
        self.path_after = path_after
        self.content_before = content_before
        self.content_after = content_after
        self.tier_before = tier_before
        self.tier_after = tier_after
        self.project = project

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Change":
        return cls(**d)


def record(action: str, changes: list[Change], note: str = "") -> str:
    """把一次操作写进日志, 返回操作 id。"""
    C.ensure_dirs()
    op_id = f"{_now()}#{_counter()}"
    entry = {
        "id": op_id,
        "ts": _now(),
        "action": action,
        "note": note,
        "changes": [c.to_dict() for c in changes],
    }
    with open(C.OPS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return op_id


_counter_val = 0


def _counter() -> int:
    global _counter_val
    _counter_val += 1
    return _counter_val


def read_log(limit: int | None = 50) -> list[dict]:
    """读取操作日志(最新在前)。"""
    if not C.OPS_LOG.exists():
        return []
    lines = C.OPS_LOG.read_text(encoding="utf-8").splitlines()
    entries = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            entries.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    entries.reverse()
    return entries[:limit] if limit else entries


def apply_reverse(entry: dict) -> list[Change]:
    """逆向还原一条操作记录里的所有文件变更。返回逆操作产生的 Change(用于再记日志)。

    不在这里碰索引, 调用方负责回退后 rebuild/upsert。
    """
    reverse_changes: list[Change] = []
    for cd in entry["changes"]:
        c = Change.from_dict(cd)
        # 正向 after 状态 → 还原回 before 状态
        # 1) 删掉 after 文件(若与 before 不同路径或 before 为空)
        if c.path_after and c.path_after != c.path_before:
            Path(c.path_after).unlink(missing_ok=True)
        # 2) 还原 before
        if c.path_before is not None and c.content_before is not None:
            p = Path(c.path_before)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(c.content_before, encoding="utf-8")
        elif c.path_before is None and c.path_after:
            # 原本是创建 → 逆操作就是删除(上面已删)
            pass
        reverse_changes.append(Change(
            path_before=c.path_after, path_after=c.path_before,
            content_before=c.content_after, content_after=c.content_before,
            tier_before=c.tier_after, tier_after=c.tier_before,
            project=c.project,
        ))
    return reverse_changes
