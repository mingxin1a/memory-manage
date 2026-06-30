# memmgr — 跨项目 Claude 记忆管理

解决"记忆很大、索引不够用"：把 Claude Code 各项目散落的记忆统一**索引、检索、治理**，
运行时按需动态召回（取代全量加载 `MEMORY.md`），并提供分层归档/可恢复的安全网与可视化面板。

全本地、不联网。`.md` 文件始终是唯一事实源，索引库随时可重建。

## 安装

```powershell
pip install -r requirements.txt   # pyyaml + streamlit
```

无需安装本包，直接在项目根目录用 `python -m memmgr ...`。

## 三件套

| 能力 | 入口 | 作用 |
|------|------|------|
| 管理后台 | `python -m memmgr panel` | 可视化：总览/检索/归档回收站/去重/提升全局 |
| 命令行 | `python -m memmgr <cmd>` | 脚本化操作，见下 |
| 运行时增强 | 两个 hook | 动态召回注入 + 会话结束维护 |

## 快速开始

```powershell
python -m memmgr scan                 # 扫描所有项目, 建中央索引
python -m memmgr stats                # 总览 + 健康度(重复/久未命中)
python -m memmgr search "MCP windows" # 跨项目混合检索
python -m memmgr panel                # 打开可视化面板
```

## 常用命令

```
scan                  重建索引(从所有 .md 文件)
stats                 总览统计 + 健康度
search <q> [-k N]     混合检索(FTS5 + 关键词 + 链接扩展)
recall <proj> <q>     模拟某项目的召回结果
archive <path>        归档(降级, 不删)
restore <path>        从归档还原
trash <path>          移入回收站
purge <path>          物理删除(仅限回收站)
pin/unpin <path>      锁定/解锁(锁定免疫自动机制)
promote <path>        提升为全局记忆(所有项目可召回)
dupes                 列疑似重复对
stale                 列建议归档项(久未命中)
undo [op_id]          撤销操作(缺省撤最近一条)
log                   操作日志
snapshot <msg>        手动 git 快照
```

## 接入 Claude Code（hooks）

把 `hooks/settings-snippet.json` 里的 `hooks` 块合并进 `~/.claude/settings.json`：

- **UserPromptSubmit → recall.py**：每轮按你的输入动态召回 top-k 相关记忆注入上下文；
  归档区里被强命中的记忆会自动"复活"。取代全量加载 `MEMORY.md`，记忆再多 token 也恒定。
- **Stop → extract.py**：会话结束时增量同步索引（收录本次新写记忆）+ git 快照留还原点。
  可选 LLM 自动抽取：设 `MEMMGR_AUTO_EXTRACT=1` 且配 `ANTHROPIC_API_KEY`，
  抽取结果落到 `pending/` 待面板确认（过质量闸，不直接进库）。

排错：给 hook 命令所在环境设 `MEMMGR_DEBUG=1`，异常会打到 stderr。

## 安全网（不怕优化掉有用记忆）

- **永不硬删**：自动机制最多把记忆降到「归档区」，物理删除只发生在回收站且需二次确认。
- **归档会复活**：归档记忆仍参与检索，被强命中自动提回 active。
- **锁定免疫**：`pin` 的记忆任何自动机制都不碰。
- **可撤销**：每步操作进日志，`undo` 逆向还原。
- **git 兜底**：批量/会话结束自动快照到 `~/.claude/memory-manager/backup`，可 `git log` 回溯。

## 数据位置

```
~/.claude/projects/<proj>/memory/*.md   各项目记忆(事实源, Claude 照常读)
~/.claude/memory/*.md                    全局记忆(scope=global)
~/.claude/memory-manager/
  index.db        中央索引(可重建)
  archive/        归档区
  trash/          回收站
  backup/         git 兜底镜像
  operations.jsonl 操作日志
  pending/        待确认的 LLM 抽取候选
```

设 `MEMMGR_CLAUDE_HOME` 可指向别的 `.claude`（测试用）。

详见 [DESIGN.md](DESIGN.md)。
