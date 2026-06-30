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

# ---- 维度: volatility(波动性) ----
# stable=稳定事实(架构/约定/schema), 不衰减也不因时间过期
# volatile=易变状态(快照/当前进度/临时), 短期到期、快速衰减
VOL_STABLE = "stable"
VOL_NORMAL = "normal"
VOL_VOLATILE = "volatile"
ALL_VOLATILITY = (VOL_STABLE, VOL_NORMAL, VOL_VOLATILE)

VOLATILE_TTL_DAYS = 14                            # volatile 超过此天数没命中 → 建议归档
# 差异化 recency 半衰期(天): stable 不衰减(None), normal/volatile 各自半衰期
VOLATILE_HALFLIFE_DAYS = 14

# ---- 维度: nature(性质) ----
# fact=长期事实, todo=待办(完成即归档), decision=决策(通常稳定且重要)
NAT_FACT = "fact"
NAT_TODO = "todo"
NAT_DECISION = "decision"
ALL_NATURE = (NAT_FACT, NAT_TODO, NAT_DECISION)

# 召回默认返回条数
DEFAULT_TOP_K = 8
LINK_EXPAND_HOPS = 1                             # [[link]] 沿边扩展跳数

# recency 衰减: 越旧/越久没碰的记忆召回分越低(乘性, 带下限, 不抹掉强相关)
# 取 last_accessed / created_at / 文件 mtime 里最新的一个当"新鲜度"
RECENCY_HALFLIFE_DAYS = 90                       # 每过这么多天, 衰减因子减半
RECENCY_FLOOR = 0.5                              # 衰减下限(再旧也保留这个比例的分)
RECENCY_PIN_EXEMPT = True                        # pinned 记忆不衰减

# 大记忆"摘要 + 明细两层"注入
INLINE_FULL_CHARS = 800                          # 正文 <= 此长度则整条注入
LEAD_CHARS = 500                                 # 超长记忆只注入这么长的"引子"


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
