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
    # 关掉行尾转换, 保证快照/还原的内容与源逐字节一致
    _git("config", "core.autocrlf", "false")
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


# ---- 列快照 / 从快照还原 ---------------------------------------------------

def list_snapshots(limit: int = 50) -> list[dict]:
    """列出快照历史(最新在前)。"""
    if not (BACKUP_DIR / ".git").exists():
        return []
    r = _git("log", f"-{limit}", "--pretty=%h%x09%ci%x09%s")
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            out.append({"hash": parts[0], "date": parts[1][:19], "msg": parts[2]})
    return out


def _target_for(rel_posix: str) -> Path | None:
    """把镜像内相对路径 (tier/project/<rel>) 映射回真实记忆位置。"""
    parts = rel_posix.split("/")
    if len(parts) < 3:
        return None
    tier, project = parts[0], parts[1]
    rel = Path(*parts[2:])
    if tier == C.STATUS_ACTIVE:
        if project == C.GLOBAL_PROJECT_ID:
            return C.GLOBAL_MEMORY_DIR / rel
        return C.PROJECTS_DIR / project / "memory" / rel
    if tier == C.STATUS_ARCHIVED:
        return C.ARCHIVE_ROOT / project / rel
    if tier == C.STATUS_TRASH:
        return C.TRASH_ROOT / project / rel
    return None


def restore_snapshot(ref: str = "HEAD", dry_run: bool = True) -> dict:
    """从某个快照(默认最新 HEAD)把记忆文件还原回真实位置。

    加性还原: 写回快照里的每个文件(不存在则建, 不同则覆盖); **不删除**快照之后
    新增的记忆(避免误删)。dry_run=True 只报告将变更项, 不动文件。
    """
    if not _available() or not (BACKUP_DIR / ".git").exists():
        return {"error": "没有可用的快照仓库", "changed": [], "restored": []}
    r = _git("ls-tree", "-r", "--name-only", ref)
    if r.returncode != 0:
        return {"error": f"无效的快照 ref: {ref}", "changed": [], "restored": []}
    files = [f for f in r.stdout.splitlines() if f.endswith(".md")]
    changed, restored = [], []
    for f in files:
        target = _target_for(f)
        if target is None:
            continue
        content = _git("show", f"{ref}:{f}").stdout
        exists = target.exists()
        if exists:
            cur = target.read_text(encoding="utf-8", errors="replace")
            if cur == content:
                continue
            kind = "overwrite"
        else:
            kind = "create"
        changed.append({"path": str(target), "kind": kind})
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            restored.append(str(target))
    return {"ref": ref, "snapshot_files": len(files),
            "changed": changed, "restored": restored, "dry_run": dry_run}
