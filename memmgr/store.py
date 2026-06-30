"""记忆文件的读写: frontmatter 解析、生命周期字段、链接提取。

`.md` 文件是唯一事实源, 索引库只是缓存。生命周期状态(status/scope/pinned...)
都写回 frontmatter 的 metadata 里, 这样索引随时可从文件重建, 不怕索引损坏。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterator

import yaml

from . import config as C

# [[link]] 形式的链接 (取里面的 slug, 去掉可能的 |别名)
_LINK_RE = re.compile(r"\[\[([^\]\|]+?)(?:\|[^\]]*)?\]\]")
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass
class Memory:
    path: str                       # 绝对路径(当前所在层级的真实文件)
    project: str                    # 项目 id, 或 __global__
    name: str
    description: str = ""
    body: str = ""
    type: str = "reference"         # user|feedback|project|reference
    # ---- 生命周期 / 作用域 ----
    status: str = C.STATUS_ACTIVE
    scope: str = C.SCOPE_PROJECT
    pinned: bool = False
    confidence: float = 0.8
    tags: list[str] = field(default_factory=list)
    # ---- 时间 / 统计 ----
    created_at: str = ""
    last_accessed: str = ""
    access_count: int = 0
    archived_at: str | None = None
    trashed_at: str | None = None
    # ---- 关系 / 解释 ----
    derived_from: list[str] = field(default_factory=list)   # 由哪些原件合并而来
    superseded_by: str | None = None                        # 被哪条新结论取代
    reason: str | None = None                               # 最近一次降级原因
    # ---- 原样保留的其它 metadata (node_type/originSessionId 等) ----
    extra: dict[str, Any] = field(default_factory=dict)
    mtime: float = 0.0              # 文件修改时间(扫描时填, 不写回)

    @property
    def links(self) -> list[str]:
        return extract_links(self.body)


def extract_links(body: str) -> list[str]:
    """从正文里抽出所有 [[name]] 链接的 slug。"""
    seen: list[str] = []
    for m in _LINK_RE.findall(body or ""):
        s = m.strip()
        if s and s not in seen:
            seen.append(s)
    return seen


# 这些 key 由 dataclass 字段直接管理, 不放进 extra
_MANAGED_META = {
    "type", "status", "scope", "pinned", "confidence", "tags",
    "created_at", "last_accessed", "access_count", "archived_at",
    "trashed_at", "derived_from", "superseded_by", "reason",
}


def parse_file(path: Path, project: str) -> Memory:
    """读取并解析一个记忆 .md 文件。容错: frontmatter 缺失/损坏也尽量返回。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    m = _FM_RE.match(raw)
    if m:
        fm_text, body = m.group(1), m.group(2)
        try:
            fm = yaml.safe_load(fm_text) or {}
            if not isinstance(fm, dict):
                fm = {}
        except yaml.YAMLError:
            fm = {}
    else:
        fm, body = {}, raw

    meta = fm.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}

    extra = {k: v for k, v in meta.items() if k not in _MANAGED_META}

    mem = Memory(
        path=str(path),
        project=project,
        name=str(fm.get("name") or path.stem),
        description=str(fm.get("description") or ""),
        body=body.strip(),
        type=str(meta.get("type") or "reference"),
        status=str(meta.get("status") or C.STATUS_ACTIVE),
        scope=str(meta.get("scope") or (
            C.SCOPE_GLOBAL if project == C.GLOBAL_PROJECT_ID else C.SCOPE_PROJECT)),
        pinned=bool(meta.get("pinned", False)),
        confidence=_as_float(meta.get("confidence"), 0.8),
        tags=_as_list(meta.get("tags")),
        created_at=str(meta.get("created_at") or ""),
        last_accessed=str(meta.get("last_accessed") or ""),
        access_count=int(meta.get("access_count") or 0),
        archived_at=_as_opt_str(meta.get("archived_at")),
        trashed_at=_as_opt_str(meta.get("trashed_at")),
        derived_from=_as_list(meta.get("derived_from")),
        superseded_by=_as_opt_str(meta.get("superseded_by")),
        reason=_as_opt_str(meta.get("reason")),
        extra=extra,
        mtime=path.stat().st_mtime,
    )
    return mem


def dump(mem: Memory) -> str:
    """把 Memory 序列化回 frontmatter + 正文文本。"""
    meta: dict[str, Any] = {}
    # node_type 放最前, 兼容 Claude 既有写法
    if "node_type" in mem.extra:
        meta["node_type"] = mem.extra["node_type"]
    meta["type"] = mem.type
    meta["status"] = mem.status
    meta["scope"] = mem.scope
    if mem.pinned:
        meta["pinned"] = True
    meta["confidence"] = round(mem.confidence, 3)
    if mem.tags:
        meta["tags"] = mem.tags
    if mem.created_at:
        meta["created_at"] = mem.created_at
    if mem.last_accessed:
        meta["last_accessed"] = mem.last_accessed
    if mem.access_count:
        meta["access_count"] = mem.access_count
    if mem.archived_at:
        meta["archived_at"] = mem.archived_at
    if mem.trashed_at:
        meta["trashed_at"] = mem.trashed_at
    if mem.derived_from:
        meta["derived_from"] = mem.derived_from
    if mem.superseded_by:
        meta["superseded_by"] = mem.superseded_by
    if mem.reason:
        meta["reason"] = mem.reason
    # 其余原样保留的 extra(除 node_type 已处理)
    for k, v in mem.extra.items():
        if k != "node_type":
            meta[k] = v

    fm = {
        "name": mem.name,
        "description": mem.description,
        "metadata": meta,
    }
    fm_text = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False,
                             default_flow_style=False).rstrip()
    return f"---\n{fm_text}\n---\n\n{mem.body.strip()}\n"


def write_file(mem: Memory, path: Path | None = None) -> Path:
    """写回磁盘。path 缺省用 mem.path。返回写入路径。"""
    target = Path(path or mem.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump(mem), encoding="utf-8")
    mem.path = str(target)
    return target


# ---- 遍历所有记忆文件 ------------------------------------------------------

def project_memory_dir(project: str) -> Path:
    if project == C.GLOBAL_PROJECT_ID:
        return C.GLOBAL_MEMORY_DIR
    return C.PROJECTS_DIR / project / "memory"


def iter_active_files() -> Iterator[tuple[Path, str]]:
    """遍历所有处于 active 层(物理在 memory 目录里)的记忆文件。

    递归扫描子目录(有的项目把记忆组织成嵌套树)。
    产出 (path, project_id)。跳过任意层级的 MEMORY.md 索引文件。
    """
    # 各项目
    if C.PROJECTS_DIR.exists():
        for proj_dir in sorted(C.PROJECTS_DIR.iterdir()):
            if not proj_dir.is_dir():
                continue
            mem_dir = proj_dir / "memory"
            if not mem_dir.is_dir():
                continue
            for f in sorted(mem_dir.rglob("*.md")):
                if f.name == C.INDEX_FILENAME:
                    continue
                yield f, proj_dir.name
    # 全局
    if C.GLOBAL_MEMORY_DIR.exists():
        for f in sorted(C.GLOBAL_MEMORY_DIR.rglob("*.md")):
            if f.name == C.INDEX_FILENAME:
                continue
            yield f, C.GLOBAL_PROJECT_ID


def iter_tier_files(root: Path) -> Iterator[tuple[Path, str]]:
    """遍历归档/回收站目录: root/<project>/**/*.md → (path, project)。"""
    if not root.exists():
        return
    for proj_dir in sorted(root.iterdir()):
        if not proj_dir.is_dir():
            continue
        for f in sorted(proj_dir.rglob("*.md")):
            yield f, proj_dir.name


def rel_under_memory(path: str | Path, project: str) -> Path:
    """记忆文件相对其 active 记忆根目录的子路径(保留嵌套结构, 用于归档/还原往返)。"""
    p = Path(path)
    root = project_memory_dir(project)
    try:
        return p.relative_to(root)
    except ValueError:
        return Path(p.name)


def tier_rel_path(path: str | Path, project: str, tier_root: Path) -> Path:
    """tier 目录(归档/回收站)里文件相对 root/<project>/ 的子路径。"""
    p = Path(path)
    try:
        return p.relative_to(tier_root / project)
    except ValueError:
        return Path(p.name)


def iter_all_files() -> Iterator[tuple[Path, str, str]]:
    """遍历全部三层。产出 (path, project, status_tier)。"""
    for p, proj in iter_active_files():
        yield p, proj, C.STATUS_ACTIVE
    for p, proj in iter_tier_files(C.ARCHIVE_ROOT):
        yield p, proj, C.STATUS_ARCHIVED
    for p, proj in iter_tier_files(C.TRASH_ROOT):
        yield p, proj, C.STATUS_TRASH


# ---- 小工具 ----------------------------------------------------------------

def _as_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return []


def _as_opt_str(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


def to_row(mem: Memory, tier: str) -> dict[str, Any]:
    """转成可写入 SQLite 的扁平 dict。"""
    d = asdict(mem)
    d.pop("extra", None)
    d["tags"] = ",".join(mem.tags)
    d["derived_from"] = ",".join(mem.derived_from)
    d["tier"] = tier
    d["links"] = ",".join(mem.links)
    return d
