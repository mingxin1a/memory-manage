"""记忆生命周期与作用域操作。

三层物理位置:
  active   →  <claude>/projects/<proj>/memory/<rel>   (或 global: <claude>/memory/)
  archived →  <claude>/memory-manager/archive/<proj>/<rel>
  trash    →  <claude>/memory-manager/trash/<proj>/<rel>

所有会改文件的操作都: 读旧内容 → 改文件 → 记可逆日志 → 同步索引。
pinned=True 的记忆受自动机制保护(本模块的 *_auto 检查会跳过)。
物理删除只允许对 trash 层执行。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from . import config as C
from . import index, ops, store
from .store import Memory


def _today() -> str:
    return datetime.now().date().isoformat()


def _tier_root(tier: str, project: str) -> Path:
    if tier == C.STATUS_ACTIVE:
        return store.project_memory_dir(project)
    if tier == C.STATUS_ARCHIVED:
        return C.ARCHIVE_ROOT / project
    return C.TRASH_ROOT / project


def _rel_under_tier(path: str, project: str, tier: str) -> Path:
    root = _tier_root(tier, project)
    try:
        return Path(path).relative_to(root)
    except ValueError:
        return Path(Path(path).name)


def _load(path: str) -> tuple[Memory, str, str]:
    """按路径加载记忆。返回 (mem, project, current_tier)。"""
    p = Path(path)
    sp = str(p)
    # 判定层级与 project
    if str(C.ARCHIVE_ROOT) in sp:
        tier = C.STATUS_ARCHIVED
        project = p.relative_to(C.ARCHIVE_ROOT).parts[0]
    elif str(C.TRASH_ROOT) in sp:
        tier = C.STATUS_TRASH
        project = p.relative_to(C.TRASH_ROOT).parts[0]
    elif str(C.GLOBAL_MEMORY_DIR) in sp and str(C.PROJECTS_DIR) not in sp:
        tier = C.STATUS_ACTIVE
        project = C.GLOBAL_PROJECT_ID
    else:
        tier = C.STATUS_ACTIVE
        project = p.relative_to(C.PROJECTS_DIR).parts[0]
    mem = store.parse_file(p, project)
    mem.status = tier
    return mem, project, tier


# ---- 三层迁移 --------------------------------------------------------------

def _change_tier(con: sqlite3.Connection, path: str, to_tier: str,
                 reason: str = "") -> str:
    mem, project, from_tier = _load(path)
    if from_tier == to_tier:
        return ""
    content_before = Path(path).read_text(encoding="utf-8", errors="replace")

    rel = _rel_under_tier(path, project, from_tier)
    dst = _tier_root(to_tier, project) / rel

    mem.status = to_tier
    mem.reason = reason or None
    if to_tier == C.STATUS_ARCHIVED:
        mem.archived_at = _today()
    elif to_tier == C.STATUS_TRASH:
        mem.trashed_at = _today()
    elif to_tier == C.STATUS_ACTIVE:
        mem.archived_at = None
        mem.trashed_at = None

    store.write_file(mem, dst)
    if str(dst) != str(path):
        Path(path).unlink(missing_ok=True)
    content_after = Path(dst).read_text(encoding="utf-8", errors="replace")

    op_id = ops.record(
        f"{from_tier}->{to_tier}",
        [ops.Change(str(path), str(dst), content_before, content_after,
                    from_tier, to_tier, project)],
        note=reason,
    )
    # 同步索引
    index.remove_path(con, str(path))
    mem.path = str(dst)
    index.upsert(con, mem, to_tier)
    return str(dst)   # 返回新路径(路径会随层级移动而变)


def archive(con, path, reason="手动归档"):
    return _change_tier(con, path, C.STATUS_ARCHIVED, reason)


def restore(con, path, reason="还原到 active"):
    return _change_tier(con, path, C.STATUS_ACTIVE, reason)


def trash(con, path, reason="移入回收站"):
    return _change_tier(con, path, C.STATUS_TRASH, reason)


def untrash(con, path, reason="从回收站还原"):
    return _change_tier(con, path, C.STATUS_ACTIVE, reason)


# ---- 原地编辑(pin / 置信度 / 字段) ----------------------------------------

def _inplace_edit(con, path, mutate, action, note=""):
    mem, project, tier = _load(path)
    content_before = Path(path).read_text(encoding="utf-8", errors="replace")
    mutate(mem)
    store.write_file(mem, path)
    content_after = Path(path).read_text(encoding="utf-8", errors="replace")
    op_id = ops.record(action, [ops.Change(
        str(path), str(path), content_before, content_after, tier, tier, project)],
        note=note)
    index.upsert(con, mem, tier)
    return op_id


def set_pinned(con, path, pinned: bool):
    return _inplace_edit(con, path, lambda m: setattr(m, "pinned", pinned),
                         "pin" if pinned else "unpin")


def set_confidence(con, path, value: float):
    return _inplace_edit(con, path, lambda m: setattr(m, "confidence", value),
                         "set_confidence", note=str(value))


def edit_body(con, path, new_desc: str | None, new_body: str | None):
    def mut(m):
        if new_desc is not None:
            m.description = new_desc
        if new_body is not None:
            m.body = new_body
    return _inplace_edit(con, path, mut, "edit")


# ---- 永久删除(仅限回收站) -------------------------------------------------

def purge(con, path) -> str:
    mem, project, tier = _load(path)
    if tier != C.STATUS_TRASH:
        raise ValueError("只能物理删除回收站里的记忆; 请先 trash()")
    content_before = Path(path).read_text(encoding="utf-8", errors="replace")
    Path(path).unlink(missing_ok=True)
    op_id = ops.record("purge", [ops.Change(
        str(path), None, content_before, None, tier, None, project)],
        note="物理删除")
    index.remove_path(con, str(path))
    return op_id


def purge_expired_trash(con, retention_days: int = C.TRASH_RETENTION_DAYS) -> list[str]:
    """物理删除回收站里超过保留期的记忆。返回被删 path 列表。需调用方先做 git 快照。"""
    cutoff = (datetime.now().date() - timedelta(days=retention_days)).isoformat()
    purged = []
    for r in index.all_rows(con, tier=C.STATUS_TRASH):
        if (r["trashed_at"] or "") and r["trashed_at"] <= cutoff and not r["pinned"]:
            purge(con, r["path"])
            purged.append(r["path"])
    return purged


# ---- 作用域: 提升到 global -------------------------------------------------

def promote_to_global(con, path, reason="提升为全局记忆") -> str:
    """把一条项目记忆提升为 global: 移到 <claude>/memory/, scope=global。"""
    mem, project, tier = _load(path)
    if project == C.GLOBAL_PROJECT_ID:
        return ""
    content_before = Path(path).read_text(encoding="utf-8", errors="replace")

    # 全局区扁平存放, 处理重名
    dst = C.GLOBAL_MEMORY_DIR / f"{mem.name}.md"
    if dst.exists() and str(dst) != str(path):
        dst = C.GLOBAL_MEMORY_DIR / f"{project}__{mem.name}.md"

    mem.scope = C.SCOPE_GLOBAL
    mem.project = C.GLOBAL_PROJECT_ID
    mem.status = C.STATUS_ACTIVE
    mem.reason = reason
    store.write_file(mem, dst)
    Path(path).unlink(missing_ok=True)
    content_after = Path(dst).read_text(encoding="utf-8", errors="replace")

    op_id = ops.record("promote_global", [ops.Change(
        str(path), str(dst), content_before, content_after,
        tier, C.STATUS_ACTIVE, project)], note=reason)
    index.remove_path(con, str(path))
    mem.path = str(dst)
    index.upsert(con, mem, C.STATUS_ACTIVE)
    return op_id


def set_tags(con, path, tags: list[str]):
    return _inplace_edit(con, path, lambda m: setattr(m, "tags", tags),
                         "set_tags", note=",".join(tags))


# ---- undo ------------------------------------------------------------------

def undo(con, op_id: str | None = None) -> dict | None:
    """撤销指定操作(缺省撤销最近一条)。返回被撤销的操作记录。"""
    log = ops.read_log(limit=None)
    if not log:
        return None
    entry = None
    if op_id:
        for e in log:
            if e["id"] == op_id:
                entry = e
                break
    else:
        entry = log[0]
    if entry is None:
        return None

    reverse_changes = ops.apply_reverse(entry)
    ops.record(f"undo:{entry['action']}", reverse_changes,
               note=f"撤销 {entry['id']}")
    # 受影响文件重新入索引(简单起见整库重建, 量小可接受)
    index.rebuild(con)
    return entry


# ---- 建议(只读, 不自动执行) ----------------------------------------------

def stale_candidates(con, days: int = C.STALE_DAYS) -> list[sqlite3.Row]:
    """长期未命中、未锁定的 active 记忆 → 建议归档(不自动执行)。"""
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    out = []
    for r in index.all_rows(con, tier=C.STATUS_ACTIVE):
        if r["pinned"]:
            continue
        la = r["last_accessed"] or ""
        if la and la < cutoff:
            out.append(r)
        elif not la and (r["created_at"] or "") and r["created_at"] < cutoff:
            out.append(r)
    return out


def duplicate_pairs(con, threshold: float = 0.82, max_pairs: int = 200) -> list[dict]:
    """找疑似重复对(描述/标题高度相似)。只读, 供去重工作台人工确认。"""
    rows = index.all_rows(con, tier=C.STATUS_ACTIVE)
    texts = [f"{r['name']} {r['description']}".lower() for r in rows]
    pairs = []
    n = len(rows)
    for i in range(n):
        a_text = texts[i]
        la = len(a_text)
        sm = SequenceMatcher(None, a_text, "")  # 复用 a 的自动结, 换 b 更快
        for j in range(i + 1, n):
            b_text = texts[j]
            # 三级廉价预筛: 长度差 → real_quick_ratio → quick_ratio → 真比
            if abs(la - len(b_text)) > max(la, len(b_text)) * 0.4:
                continue
            sm.set_seq2(b_text)
            if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                continue
            ratio = sm.ratio()
            if ratio >= threshold:
                pairs.append({"a": rows[i], "b": rows[j], "ratio": round(ratio, 3)})
                if len(pairs) >= max_pairs:
                    return sorted(pairs, key=lambda x: x["ratio"], reverse=True)
    return sorted(pairs, key=lambda x: x["ratio"], reverse=True)
