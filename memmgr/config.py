"""路径与常量配置。所有可调项集中在这里。"""

from __future__ import annotations

import os
from pathlib import Path

# ---- 根目录 ----------------------------------------------------------------

# 允许用环境变量覆盖, 方便测试 (指向一个假的 .claude)
CLAUDE_HOME = Path(os.environ.get("MEMMGR_CLAUDE_HOME", Path.home() / ".claude"))

PROJECTS_DIR = CLAUDE_HOME / "projects"          # 各项目: <proj>/memory/*.md
GLOBAL_MEMORY_DIR = CLAUDE_HOME / "memory"       # 全局(scope=global)记忆

# 本工具自己的数据区
MANAGER_DIR = CLAUDE_HOME / "memory-manager"
INDEX_DB = MANAGER_DIR / "index.db"
ARCHIVE_ROOT = MANAGER_DIR / "archive"           # 归档区: archive/<project>/*.md
TRASH_ROOT = MANAGER_DIR / "trash"               # 回收站: trash/<project>/*.md
OPS_LOG = MANAGER_DIR / "operations.jsonl"       # 操作日志(每行一条, 可 undo)

# ---- 常量 ------------------------------------------------------------------

INDEX_FILENAME = "MEMORY.md"                      # 各项目的索引文件, 不当作记忆条目
GLOBAL_PROJECT_ID = "__global__"                 # 全局记忆在索引里的 project 值

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUS_TRASH = "trash"
ALL_STATUSES = (STATUS_ACTIVE, STATUS_ARCHIVED, STATUS_TRASH)

SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"
SCOPE_SHARED = "shared"

# 遗忘/归档默认阈值 (天) —— 仅供"建议", 自动机制只降级不删除
STALE_DAYS = 90                                  # 超过这么久没命中 → 建议归档
TRASH_RETENTION_DAYS = 30                        # 回收站保留期, 之后才允许物理删除

# 召回默认返回条数
DEFAULT_TOP_K = 8
LINK_EXPAND_HOPS = 1                             # [[link]] 沿边扩展跳数


def ensure_dirs() -> None:
    """确保本工具的数据目录存在。"""
    for d in (MANAGER_DIR, ARCHIVE_ROOT, TRASH_ROOT, GLOBAL_MEMORY_DIR):
        d.mkdir(parents=True, exist_ok=True)


def decode_project_id(project_id: str) -> str:
    """把 'D--work-memorymanage' 这种编码尽量还原成可读路径(仅展示用)。

    Claude 的编码规则不完全可逆(- 既是分隔符又可能是原路径里的字符),
    这里做尽力而为的展示, 不用于任何文件定位。
    """
    if project_id == GLOBAL_PROJECT_ID:
        return "🌐 全局 (global)"
    # 形如 D--work-memorymanage → D:\work\memorymanage (尽力)
    s = project_id
    if len(s) >= 2 and s[1:3] == "--":
        s = s[0] + ":\\" + s[3:]
    s = s.replace("-", "\\")
    return s
