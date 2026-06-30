# 设计文档

## 问题

Claude Code 的记忆按项目分散存（`~/.claude/projects/<proj>/memory/*.md`），并把每个项目的
`MEMORY.md` 索引**全量加载进上下文**。记忆累积到几百上千条后：

1. 索引全量加载 → token 线性膨胀，模型还要自己在长列表里挑相关项，挑不准
2. 召回粗糙 → 没有相关性排序，记忆越多信噪比越差
3. 记忆是孤岛 → 项目间无法共享，该全局的偏好困在局部
4. 只增不减 → 过期/重复一直留着；且没有跨项目的全局视图

## 核心思路

**在不破坏 Claude 按项目读取的前提下，加一个跨项目聚合层。** `.md` 仍是唯一事实源、仍按项目存；
另建一个中央 SQLite 索引统管全部，并把"全量加载索引"换成"按当前输入动态召回 top-k"。

## 架构

```
运行时增强(hooks)                      管理后台(本地)
  UserPromptSubmit → 召回注入 top-k      Streamlit 面板 / CLI
  Stop → 增量同步 + git 快照                 ↓ 读/写
        ↓                              中央索引 index.db (SQLite+FTS5)
   各项目 *.md (事实源) ←─ 扫描/回写 ─→  archive/ trash/ backup/ ops.log
```

## 关键设计

### 存储：双轨
- `.md` 文件：唯一事实源，人可读、可 diff。生命周期状态(status/scope/pinned…)写回 frontmatter。
- `index.db`：缓存，随时可从文件 `rebuild()`。访问统计单独存 `access` 表，不随文件重写丢失。

### 检索：混合
- FTS5 **trigram** tokenizer（对中英文混合子串匹配友好）+ BM25 列加权(name>desc>body)
- 短词/补召回走 LIKE 兜底
- 元数据过滤(project/scope/type/tier) + pinned/confidence 微调
- `[[link]]` 图扩展一跳，补"语义不近但强相关"的记忆
- **recency 衰减**：取 `last_accessed/created_at/mtime` 里最新的当"新鲜度"，
  按半衰期(默认 90 天)做乘性衰减 ∈ [0.5, 1.0]，旧/久未碰的下沉但不抹掉强相关；
  pinned 免疫。解决"同一事实多版本，旧版本不该和新版本平起平坐"。

### 注入：摘要 + 明细两层
召回 hook 不再整条灌大记忆：标题 + 描述(人工精炼一句话)是摘要层；正文 ≤800 字整条给，
超长则只给干净截断(段落/句子边界)的"引子" + 指向原文的 Read 提示。
单条 11K 字符的 hub 记忆从灌 11K 降到 ~500 字 + 指针，token 省一个量级。

### 维度：volatility(波动性) 与 nature(性质)
逻辑分类叠加在 type/scope 之上(不动物理结构, 一条记忆可多维)。
- **volatility** = stable | normal | volatile：
  - 驱动差异化 **TTL**(stale 检测)：stable 免疫；volatile 短(默认 14 天)；normal 默认 90 天
  - 驱动差异化 **recency 半衰期**：stable 不衰减；volatile 衰减快(默认 14 天)；normal 90 天
  - 解决"架构约定"和"6-24 当前快照"不该同样对待
- **nature** = fact | todo | decision：
  - todo 有独立生命周期：面板独立视图 + 「完成」即归档，不污染日常召回
  - decision 通常稳定且重要
- **启发式推断**(`classify`)：按关键词/日期名给"建议"，dry-run 默认，可逐条或批量采纳
  (写入前自动 git 快照, 每条可 undo)。实测 609 条给出 393 条合理建议。

### 作用域：三层
- `project`：仅本项目；`global`(存 `~/.claude/memory/`)：所有项目召回；`shared`：按 tag 共享
- `promote` 一键把项目记忆提升为全局

### 生命周期：分层降级，永不硬删
```
active ──(久未命中/低置信/被合并)──▶ archived ──(确认/超期)──▶ trash ──(确认+保留期)──▶ 物理删除
  ▲                                    │
  └────────── 强命中自动复活 ◀──────────┘
```
- 自动机制最多降到 archived；进 trash、清 trash 都需人工确认
- 安全网：归档复活、合并保留原件、pinned 免疫、操作日志可 undo、git 兜底

### Hooks
- **recall**（UserPromptSubmit）：召回当前项目 + global 的 top-k 注入；归档强命中(相对阈值)复活；
  记访问；任何异常静默放行，绝不阻塞输入。
- **extract**（Stop）：增量 `sync()` 收录新记忆 + git 快照。可选 LLM 抽取→pending 待审。

## 技术栈与取舍

- **Python**：FTS5 内置零依赖；trigram 解决中文；生态成熟。
- **第一版不上向量**：BM25+元数据已覆盖大部分召回；向量作为后续可选第二路（`sqlite-vec`），
  价值是补"换说法/概念近似"的盲区，但核心收益在去重/rerank/遗忘，先做这些。
- **不要常驻服务**：FTS5 冷启动快；hook 用自包含脚本，召回 <0.5s、会话结束维护 ~2s。
- **Streamlit 面板**：纯 Python 快速出管理界面；图谱视图(关系可视化)留待后续 FastAPI+vis-network。

## 性能（实测 608 条真实记忆）
- 全量 `scan`/`rebuild`：~5s（解析 yaml + 入库）
- 增量 `sync`（无变化）：~1.3s
- 召回 hook：<0.5s
- git 增量快照（无变化）：~0.2s
- 全量去重(O(n²) + 三级廉价预筛)：~7s（仅面板/CLI 按需）

## 后续可选（现用现加）
- 向量检索第二路 + rerank
- 关系图可视化页
- 合并(merge)而非仅归档其一：生成摘要 + `derived_from` 链接
- LLM 抽取默认开启 + 更细的质量闸
- 版本链 `supersedes`：同一事实新版本自动取代旧版本（recency 之上更显式）
- 实体图（按表/服务/文件名反查相关记忆）
