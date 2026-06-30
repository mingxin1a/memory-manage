# -*- coding: utf-8 -*-
"""Streamlit 可视化管理面板。

启动: python -m memmgr panel   (等价 streamlit run panel/app.py)
本地、不联网。所有破坏性操作只降级不删除, 永久删除需在回收站二次确认。
"""

import sys
from pathlib import Path

# 允许直接 streamlit run 时也能 import memmgr
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from memmgr import index, lifecycle, retrieval, ops, gitbackup, config as C

st.set_page_config(page_title="Claude 记忆管理", page_icon="🧠", layout="wide")


def con():
    return index.connect()


def proj_label(p):
    return C.decode_project_id(p)


def toast(msg):
    st.session_state["_toast"] = msg


if "_toast" in st.session_state:
    st.success(st.session_state.pop("_toast"))

# ---- 侧边栏 ----------------------------------------------------------------
st.sidebar.title("🧠 记忆管理")
page = st.sidebar.radio(
    "视图",
    ["总览 Dashboard", "检索 / 浏览", "归档 / 回收站", "去重工作台", "操作日志"],
)

st.sidebar.divider()
if st.sidebar.button("🔄 重建索引 (scan)"):
    st_ = index.rebuild(con())
    toast(f"已重建: active={st_['active']} archived={st_['archived']} trash={st_['trash']}")
    st.rerun()
if st.sidebar.button("📸 git 快照"):
    h = gitbackup.snapshot_commit("manual snapshot from panel")
    toast(f"git 快照: {h or '无变更/git不可用'}")
st.sidebar.caption(f"索引库: {C.INDEX_DB}")


# ---- 记忆卡片(带操作) ------------------------------------------------------
def render_card(r, ctx=""):
    c = con()
    pin = "🔒 " if r["pinned"] else ""
    scope_badge = {"global": "🌐", "shared": "🔗", "project": "📁"}.get(r["scope"], "")
    with st.container(border=True):
        st.markdown(f"**{pin}{scope_badge} {r['name']}**  ·  `{r['type']}`  ·  "
                    f"{proj_label(r['project'])}")
        st.caption(r["description"] or "(无描述)")
        with st.expander("正文 / 详情"):
            st.code(r["path"], language=None)
            st.markdown(r["body"][:3000] or "_(空)_")
            if r["reason"]:
                st.info(f"最近降级原因: {r['reason']}")
            meta = (f"confidence={r['confidence']} · access={r['access_count']} · "
                    f"last={r['last_accessed'] or '?'} · created={r['created_at'] or '?'}")
            st.caption(meta)

        cols = st.columns(6)
        key = f"{ctx}_{r['path']}"
        tier = r["tier"]
        if tier == C.STATUS_ACTIVE:
            if cols[0].button("📥 归档", key="ar_" + key):
                lifecycle.archive(c, r["path"]); toast("已归档"); st.rerun()
            if cols[1].button("🗑 回收", key="tr_" + key):
                lifecycle.trash(c, r["path"]); toast("已移入回收站"); st.rerun()
            if r["pinned"]:
                if cols[2].button("🔓 解锁", key="up_" + key):
                    lifecycle.set_pinned(c, r["path"], False); toast("已解锁"); st.rerun()
            else:
                if cols[2].button("🔒 锁定", key="pi_" + key):
                    lifecycle.set_pinned(c, r["path"], True); toast("已锁定(免疫自动机制)"); st.rerun()
            if r["scope"] != "global":
                if cols[3].button("🌐 提升全局", key="pg_" + key):
                    lifecycle.promote_to_global(c, r["path"]); toast("已提升为全局记忆"); st.rerun()
        elif tier == C.STATUS_ARCHIVED:
            if cols[0].button("♻️ 还原", key="re_" + key):
                lifecycle.restore(c, r["path"]); toast("已还原到 active"); st.rerun()
            if cols[1].button("🗑 回收", key="tr_" + key):
                lifecycle.trash(c, r["path"]); toast("已移入回收站"); st.rerun()
        elif tier == C.STATUS_TRASH:
            if cols[0].button("♻️ 还原", key="ut_" + key):
                lifecycle.untrash(c, r["path"]); toast("已还原"); st.rerun()
            st.session_state.setdefault("_confirm_purge", set())
            if cols[1].button("❌ 永久删除", key="pu_" + key):
                st.session_state["_confirm_purge"].add(r["path"]); st.rerun()
            if r["path"] in st.session_state.get("_confirm_purge", set()):
                if cols[2].button("⚠️ 确认删除", key="cpu_" + key):
                    lifecycle.purge(c, r["path"])
                    st.session_state["_confirm_purge"].discard(r["path"])
                    toast("已物理删除(git 仍有兜底)"); st.rerun()


# ================= 页面 =====================================================
if page == "总览 Dashboard":
    c = con()
    s = index.stats(c)
    st.header("总览")
    m = st.columns(4)
    m[0].metric("记忆总数", s["total"])
    m[1].metric("活跃 active", s["by_tier"].get("active", 0))
    m[2].metric("归档 archived", s["by_tier"].get("archived", 0))
    m[3].metric("回收站 trash", s["by_tier"].get("trash", 0))

    st.subheader("健康度")
    stale = lifecycle.stale_candidates(c)
    dupes = lifecycle.duplicate_pairs(c, max_pairs=100)
    h = st.columns(3)
    h[0].metric("🔒 已锁定", s["pinned"])
    h[1].metric("⏳ 建议归档(久未命中)", len(stale))
    h[2].metric("👯 疑似重复对", len(dupes))
    if stale:
        st.caption("→ 「检索/浏览」里可逐条归档；自动机制只会建议、不会替你删。")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("按项目分布 (active)")
        data = {proj_label(k): v for k, v in s["by_project"].items()}
        st.bar_chart(data)
    with col2:
        st.subheader("按类型 / 作用域")
        st.bar_chart(s["by_type"])
        st.write("作用域:", s["by_scope"])


elif page == "检索 / 浏览":
    c = con()
    st.header("检索 / 浏览")
    q = st.text_input("🔎 跨项目检索 (留空=按过滤条件浏览)", "")
    fcol = st.columns(4)
    s = index.stats(c)
    projects = ["(全部)"] + list(s["by_project"].keys())
    proj = fcol[0].selectbox("项目", projects, format_func=lambda p: p if p == "(全部)" else proj_label(p))
    typ = fcol[1].selectbox("类型", ["(全部)", "user", "feedback", "project", "reference"])
    scope = fcol[2].selectbox("作用域", ["(全部)", "project", "global", "shared"])
    topk = fcol[3].slider("最多显示", 10, 200, 30)

    pf = None if proj == "(全部)" else [proj]
    tf = None if typ == "(全部)" else [typ]
    scf = None if scope == "(全部)" else [scope]

    if q.strip():
        hits = retrieval.search(c, q, top_k=topk, projects=pf, types=tf, scopes=scf)
        st.caption(f"{len(hits)} 条结果")
        for h in hits:
            render_card(h.row, ctx="search")
    else:
        rows = index.all_rows(c, tier=C.STATUS_ACTIVE)
        if pf:
            rows = [r for r in rows if r["project"] in pf]
        if tf:
            rows = [r for r in rows if r["type"] in tf]
        if scf:
            rows = [r for r in rows if r["scope"] in scf]
        st.caption(f"{len(rows)} 条 (显示前 {topk})")
        for r in rows[:topk]:
            render_card(r, ctx="browse")


elif page == "归档 / 回收站":
    c = con()
    st.header("归档 / 回收站")
    tab1, tab2 = st.tabs(["📥 归档区 (archived)", "🗑 回收站 (trash)"])
    with tab1:
        rows = index.all_rows(c, tier=C.STATUS_ARCHIVED)
        st.caption(f"{len(rows)} 条归档。归档不丢, 强命中会自动复活; 也可手动还原。")
        for r in rows:
            render_card(r, ctx="arch")
    with tab2:
        rows = index.all_rows(c, tier=C.STATUS_TRASH)
        st.caption(f"{len(rows)} 条在回收站。保留期 {C.TRASH_RETENTION_DAYS} 天, 永久删除需逐条确认。")
        if rows:
            with st.expander("⚠️ 清空已超保留期的回收项"):
                st.write("会先做 git 快照再物理删除超期且未锁定的项。")
                if st.button("执行清理"):
                    gitbackup.snapshot_commit("before purge_expired_trash")
                    purged = lifecycle.purge_expired_trash(c)
                    toast(f"已清理 {len(purged)} 条"); st.rerun()
        for r in rows:
            render_card(r, ctx="trash")


elif page == "去重工作台":
    c = con()
    st.header("去重工作台")
    thr = st.slider("相似度阈值", 0.70, 0.98, 0.82, 0.02)
    pairs = lifecycle.duplicate_pairs(c, threshold=thr)
    st.caption(f"{len(pairs)} 对疑似重复。合并 = 归档其中一条(保留另一条), 原件进归档区可还原。")
    for i, p in enumerate(pairs):
        a, b = p["a"], p["b"]
        with st.container(border=True):
            st.markdown(f"**相似度 {p['ratio']:.2f}**")
            cc = st.columns(2)
            for col, m, other, tag in [(cc[0], a, b, "A"), (cc[1], b, a, "B")]:
                with col:
                    st.markdown(f"**{tag}: {m['name']}**")
                    st.caption(f"{proj_label(m['project'])} · {m['type']}")
                    st.caption(m["description"][:120])
                    if st.button(f"归档 {tag}(保留另一条)", key=f"dup_{i}_{tag}"):
                        lifecycle.archive(c, m["path"], reason=f"去重: 与 {other['name']} 重复")
                        toast(f"已归档 {m['name']}"); st.rerun()


elif page == "操作日志":
    st.header("操作日志")
    st.caption("每步可撤销。撤销会逆向还原文件并重建索引。")
    log = ops.read_log(limit=80)
    if not log:
        st.info("暂无操作记录")
    for e in log:
        cols = st.columns([3, 2, 3, 2])
        cols[0].write(f"**{e['action']}**")
        cols[1].write(e["ts"])
        cols[2].caption(f"{len(e['changes'])}文件 · {e.get('note','')}")
        if not e["action"].startswith("undo"):
            if cols[3].button("↩️ 撤销", key="undo_" + e["id"]):
                lifecycle.undo(con(), e["id"]); toast("已撤销"); st.rerun()
