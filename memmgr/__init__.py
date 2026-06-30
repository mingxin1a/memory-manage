"""memmgr — 跨项目 Claude 记忆管理工具。

能力:
  - 扫描所有 ~/.claude/projects/*/memory 下的记忆, 建中央 SQLite/FTS5 索引
  - 混合检索(BM25 + 元数据过滤 + [[link]] 图扩展)
  - 生命周期: active / archived / trash 三层, pinned 免疫, 可还原
  - 操作日志 + undo, 批量操作前 git 兜底
  - 作用域: project / global / shared, 可提升到 global
"""

__version__ = "0.1.0"
