"""混合检索: FTS5(trigram, BM25 加权) + 关键词 LIKE 兜底 + 元数据过滤 +
[[link]] 图扩展一跳。CLI / hook / 面板共用同一套。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from . import config as C


@dataclass
class Hit:
    row: sqlite3.Row
    score: float
    why: str = ""        # 命中来源: fts / like / link

    def __getitem__(self, k):
        return self.row[k]


# 列权重: 命中标题/描述比命中正文更重要
_BM25_WEIGHTS = (10.0, 5.0, 1.0)   # name, description, body

_TERM_SPLIT = re.compile(r"[\s,，。、;；:：/]+")


def _terms(query: str) -> list[str]:
    return [t for t in _TERM_SPLIT.split(query.strip()) if t]


def _parse_date(s) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _freshness_date(row) -> date | None:
    """记忆的"新鲜度"日期: last_accessed / created_at / 文件 mtime 里最新的一个。"""
    cands = [_parse_date(row["last_accessed"]), _parse_date(row["created_at"])]
    try:
        mt = row["mtime"]
        if mt:
            cands.append(datetime.fromtimestamp(float(mt)).date())
    except (KeyError, IndexError, ValueError, OSError, TypeError):
        pass
    cands = [d for d in cands if d]
    return max(cands) if cands else None


def _halflife_for(row) -> float | None:
    """按 volatility 取 recency 半衰期: stable 不衰减(None), volatile 衰减快。"""
    vol = (row["volatility"] if "volatility" in row.keys() else None) or C.VOL_NORMAL
    if vol == C.VOL_STABLE:
        return None
    if vol == C.VOL_VOLATILE:
        return C.VOLATILE_HALFLIFE_DAYS
    return C.RECENCY_HALFLIFE_DAYS


def _recency_mult(row, today: date) -> float:
    """recency 乘性因子 ∈ [RECENCY_FLOOR, 1.0]。越新越接近 1, 越旧越接近下限。

    stable 记忆不衰减; volatile 半衰期短, 旧的易变状态迅速下沉。
    """
    if C.RECENCY_PIN_EXEMPT and row["pinned"]:
        return 1.0
    halflife = _halflife_for(row)
    if halflife is None:
        return 1.0
    d = _freshness_date(row)
    if d is None:
        return 1.0
    age = max(0, (today - d).days)
    factor = 0.5 ** (age / halflife)
    return C.RECENCY_FLOOR + (1.0 - C.RECENCY_FLOOR) * factor


def _match_expr(terms: list[str]) -> str:
    """构造 FTS5 MATCH 串: 长度>=3 的词作为短语 OR 连接(trigram 最短匹配单位为 3)。"""
    phrases = []
    for t in terms:
        if len(t) >= 3:
            phrases.append('"%s"' % t.replace('"', '""'))
    return " OR ".join(phrases)


def _tier_filter(tiers: list[str]) -> tuple[str, list]:
    qs = ",".join("?" * len(tiers))
    return f"tier IN ({qs})", list(tiers)


def search(
    con: sqlite3.Connection,
    query: str,
    *,
    top_k: int = C.DEFAULT_TOP_K,
    tiers: list[str] | None = None,
    projects: list[str] | None = None,
    scopes: list[str] | None = None,
    types: list[str] | None = None,
    expand_links: bool = True,
) -> list[Hit]:
    """混合检索, 返回按相关度排序的 Hit 列表。

    tiers 缺省只搜 active。projects/scopes/types 为可选白名单过滤。
    """
    tiers = tiers or [C.STATUS_ACTIVE]
    terms = _terms(query)
    if not terms:
        return []

    where = []
    params: list = []
    tf, tp = _tier_filter(tiers)
    where.append(tf)
    params += tp
    if projects:
        where.append("m.project IN (%s)" % ",".join("?" * len(projects)))
        params += projects
    if scopes:
        where.append("m.scope IN (%s)" % ",".join("?" * len(scopes)))
        params += scopes
    if types:
        where.append("m.type IN (%s)" % ",".join("?" * len(types)))
        params += types
    where_sql = " AND ".join(where)

    hits: dict[str, Hit] = {}

    # --- 1) FTS5 / BM25 ---
    match = _match_expr(terms)
    if match:
        sql = f"""
            SELECT m.*, bm25(memories_fts, ?, ?, ?) AS bm
            FROM memories_fts
            JOIN memories m ON m.id = memories_fts.rowid
            WHERE memories_fts MATCH ? AND {where_sql}
            ORDER BY bm
            LIMIT ?
        """
        rows = con.execute(
            sql, [*_BM25_WEIGHTS, match, *params, top_k * 4]
        ).fetchall()
        for r in rows:
            # bm25 越小越相关 → 取负作为正向分数
            hits[r["path"]] = Hit(r, score=-float(r["bm"]), why="fts")

    # --- 2) LIKE 兜底(短词 / 补召回) ---
    like_terms = [t for t in terms if len(t) < 3] or terms
    like_clauses = []
    like_params: list = []
    for t in like_terms:
        like_clauses.append("(m.name LIKE ? OR m.description LIKE ? OR m.body LIKE ?)")
        like_params += [f"%{t}%"] * 3
    if like_clauses:
        sql = f"""
            SELECT m.* FROM memories m
            WHERE ({' OR '.join(like_clauses)}) AND {where_sql}
            LIMIT ?
        """
        rows = con.execute(sql, [*like_params, *params, top_k * 4]).fetchall()
        for r in rows:
            if r["path"] not in hits:
                # LIKE 命中给个基础分(按命中字段加权)
                base = _like_score(r, like_terms)
                hits[r["path"]] = Hit(r, score=base, why="like")

    # pinned 与 confidence 微调
    for h in hits.values():
        if h.row["pinned"]:
            h.score += 2.0
        h.score += float(h.row["confidence"] or 0) * 0.5

    # recency 衰减(乘性, 带下限): 旧/久未碰的记忆排名下沉, 但不抹掉强相关
    today = datetime.now().date()
    for h in hits.values():
        h.score *= _recency_mult(h.row, today)

    ranked = sorted(hits.values(), key=lambda h: h.score, reverse=True)[:top_k]

    # --- 3) [[link]] 图扩展一跳 ---
    if expand_links and ranked:
        _expand(con, ranked, hits, tiers, top_k)
        ranked = sorted(hits.values(), key=lambda h: h.score, reverse=True)
        # 把扩展项接在原始 top_k 之后, 不挤掉强命中
        primary = [h for h in ranked if h.why != "link"][:top_k]
        extra = [h for h in ranked if h.why == "link"][:max(0, top_k // 2)]
        return primary + extra

    return ranked


def _expand(con, ranked, hits, tiers, top_k):
    """对当前 top 结果, 把它们 [[link]] 指向的、以及指向它们的记忆补进来(低分)。"""
    names = {h.row["name"] for h in ranked}
    linked_names: set[str] = set()
    for h in ranked:
        for n in (h.row["links"] or "").split(","):
            n = n.strip()
            if n and n not in names:
                linked_names.add(n)
    if not linked_names:
        return
    tf, tp = _tier_filter(tiers)
    qs = ",".join("?" * len(linked_names))
    sql = f"SELECT * FROM memories WHERE name IN ({qs}) AND {tf}"
    for r in con.execute(sql, [*linked_names, *tp]).fetchall():
        if r["path"] not in hits:
            hits[r["path"]] = Hit(r, score=0.3, why="link")


def _like_score(row, terms: list[str]) -> float:
    s = 0.0
    name = (row["name"] or "").lower()
    desc = (row["description"] or "").lower()
    body = (row["body"] or "").lower()
    for t in terms:
        tl = t.lower()
        if tl in name:
            s += 3.0
        if tl in desc:
            s += 2.0
        if tl in body:
            s += 1.0
    return s


def recall_for_project(
    con: sqlite3.Connection,
    project: str,
    query: str,
    *,
    top_k: int = C.DEFAULT_TOP_K,
) -> list[Hit]:
    """给定项目的召回: 该项目 active 记忆 + 所有 global + 匹配标签的 shared。

    用于 UserPromptSubmit hook 的动态注入。
    """
    # 该项目 + 全局, 一并检索(scope 由数据决定)
    proj_hits = search(
        con, query, top_k=top_k,
        projects=[project, C.GLOBAL_PROJECT_ID],
        tiers=[C.STATUS_ACTIVE],
    )
    return proj_hits
