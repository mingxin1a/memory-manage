"""git 终极兜底: 把所有记忆文件镜像进一个独立 git 仓库并提交。

即便操作日志/回收站都失效, 也能 `git log` 翻历史恢复。best-effort:
git 不可用就静默跳过, 不影响主流程。批量/破坏性操作前调用一次。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config as C
from . import store

BACKUP_DIR = C.MANAGER_DIR / "backup"


def _changed(src: Path, dst: Path) -> bool:
    """src 相对镜像 dst 是否需要重新复制(不存在或大小/mtime 不同)。"""
    if not dst.exists():
        return True
    try:
        ss, ds = src.stat(), dst.stat()
        return ss.st_size != ds.st_size or int(ss.st_mtime) != int(ds.st_mtime)
    except OSError:
        return True


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=BACKUP_DIR,
        capture_output=True, text=True, encoding="utf-8",
    )


def _available() -> bool:
    return shutil.which("git") is not None


def _ensure_repo() -> bool:
    if not _available():
        return False
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not (BACKUP_DIR / ".git").exists():
        r = _git("init", "-q")
        if r.returncode != 0:
            return False
        _git("config", "user.email", "memmgr@local")
        _git("config", "user.name", "memmgr")
    return True


def snapshot_commit(message: str) -> str | None:
    """把当前三层所有记忆文件镜像进 backup 仓库并提交。返回 commit 短哈希或 None。"""
    if not _ensure_repo():
        return None

    # 增量镜像: 只复制新增/变化的文件, 再清理孤儿(源已不存在的镜像)。
    wanted: set[Path] = set()
    for path, project, tier in store.iter_all_files():
        src = Path(path)
        rel = src.name
        try:
            if tier == C.STATUS_ACTIVE:
                rel = str(store.rel_under_memory(src, project))
            else:
                root = C.ARCHIVE_ROOT if tier == C.STATUS_ARCHIVED else C.TRASH_ROOT
                rel = str(store.tier_rel_path(src, project, root))
        except Exception:
            rel = src.name
        dst = BACKUP_DIR / tier / project / rel
        wanted.add(dst)
        try:
            if _changed(src, dst):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        except OSError:
            pass

    # 清理孤儿
    for child in BACKUP_DIR.rglob("*.md"):
        if ".git" in child.parts:
            continue
        if child not in wanted:
            child.unlink(missing_ok=True)

    _git("add", "-A")
    # 没有变更则不提交
    status = _git("status", "--porcelain")
    if not status.stdout.strip():
        return None
    r = _git("commit", "-q", "-m", message)
    if r.returncode != 0:
        return None
    h = _git("rev-parse", "--short", "HEAD")
    return h.stdout.strip() or None
