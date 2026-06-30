# -*- coding: utf-8 -*-
"""Streamlit 可视化管理面板。

启动: memmgr panel   (等价 streamlit run panel/app.py)
本地、不联网。所有破坏性操作只降级不删除, 永久删除需在回收站二次确认。
"""

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from memmgr import index, lifecycle, retrieval, ops, gitbackup, config as C

st.set_page_config(page_title="Claude 记忆管理", page_icon="🧠", layout="wide")

# ---- 样式 ------------------------------------------------------------------
st.markdown("""
<style>
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1200px;}
h1, h2, h3 {letter-spacing:.2px;}
/* 卡片(给 border 容器加圆角/呼吸感/悬停) */
div[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 12px !important; border-color:#262b36 !important;
  background:#141821; transition:border-color .15s ease;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {border-color:#36405a !important;}
/* 按钮 */
.stButton button {
  border-radius:8px; border:1px solid #2a3040; background:#1b2030;
  padding:.28rem .7rem; font-size:.82rem; transition:all .12s ease;
}
.stButton button:hover {border-color:#6c8cff; color:#cdd6ff;}
/* 指标卡 */
div[data-testid="stMetric"] {
  background:#141821; border:1px solid #232838; border-radius:12px;
  padding:14px 16px;
}
div[data-testid="stMetricValue"] {font-size:1.7rem;}
/* pill 徽章 */
.mm-pill {display:inline-block; padding:1px 9px; border-radius:999px;
  font-size:.72rem; font-weight:600; margin-right:5px; line-height:1.55;
  vertical-align:middle; border:1px solid rgba(255,255,255,.06);}
.mm-head {display:flex; align-items:center; flex-wrap:wrap; gap:4px; margin-bottom:1px;}
.mm-name {font-size:1.02rem; font-weight:700; margin-right:8px; color:#eef1f7;}
.mm-proj {color:#7b8398; font-size:.78rem; margin-bottom:2px;}
.mm-desc {color:#aeb6c8; font-size:.9rem; margin:.1rem 0 .2rem;}
hr {margin:.6rem 0;}
</style>
""", unsafe_allow_html=True)


def con():
    return index.connect()


def proj_label(p):
    return C.decode_project_id(p)


def toast(msg):
    st.session_state["_toast"] = msg


# 徽章配色: (背景, 前景)
_PILL = {
    "todo": ("#3a2e12", "#ffcf6b"), "decision": ("#2a2440", "#c4a7ff"),
    "stable": ("#15301f", "#7ee0a1"), "volatile": ("#3a1c1c", "#ff9b9b"),
    "global": ("#152a3a", "#7cc4ff"), "shared": ("#123230", "#7fe0d4"),
    "project": ("#20242e", "#aab2c5"), "pin": ("#332b10", "#ffd86b"),
    "type": ("#1c2230", "#9fb0d0"),
    "active": ("#15301f", "#7ee0a1"), "archived": ("#3a2e12", "#ffcf6b"),
    "trash": ("#2a2a2a", "#b9b9b9"),
}


def pill(text, kind):
    bg, fg = _PILL.get(kind, ("#20242e", "#aab2c5"))
    return (f'<span class="mm-pill" style="background:{bg};color:{fg}">'
            f'{html.escape(str(text))}</span>')


def pill_row(mapping, kind_map=None):
    """把 {key:count} 渲染成一排 pill。"""
    parts = []
    for k, v in mapping.items():
        knd = (kind_map or {}).get(k, k if k in _PILL else "type")
        parts.append(pill(f"{k} · {v}", knd))
    return "<div>" + "".join(parts) + "</div>"


if "_toast" in st.session_state:
    st.success(st.session_state.pop("_toast"))

# ---- 侧边栏 ----------------------------------------------------------------
st.sidebar.markdown("### 🧠 记忆管理")
st.sidebar.caption("跨项目 Claude 记忆治理")
page = st.sidebar.radio(
    "视图",
    ["总览 Dashboard", "检索 / 浏览", "待办 TODO", "分类建议",
     "归档 / 回收站", "去重工作台", "操作日志"],
    label_visibility="collapsed",
)

st.sidebar.divider()
if st.sidebar.button("🔄 重建索引", use_container_width=True):
    st_ = index.rebuild(con())
    toast(f"已重建: active={st_['active']} archived={st_['archived']} trash={st_['trash']}")
    st.rerun()
if st.sidebar.button("📸 git 快照", use_container_width=True):
    h = gitbackup.snapshot_commit("manual snapshot from panel")
    toast(f"git 快照: {h or '无变更/git不可用'}")
st.sidebar.caption(f"📁 {C.INDEX_DB}")


# ---- 记忆卡片 --------------------------------------------------------------
def render_card(r, ctx=""):
    c = con()
    scope_txt = {"global": "🌐 global", "shared": "🔗 shared", "project": "📁 project"}.get(r["scope"], r["scope"])
    with st.container(border=True):
        # 头部: 名称 + 彩色 pill
        pills = []
        if r["pinned"]:
            pills.append(pill("🔒 pinned", "pin"))
        pills.append(pill(scope_txt, r["scope"]))
        if r["nature"] and r["nature"] != "fact":
            pills.append(pill(r["nature"], r["nature"]))
        if r["volatility"] and r["volatility"] != "normal":
            pills.append(pill(r["volatility"], r["volatility"]))
        pills.append(pill(r["type"], "type"))
        if r["tier"] != "active":
            pills.append(pill(r["tier"], r["tier"]))
        st.markdown(
            f'<div class="mm-head"><span class="mm-name">{html.escape(r["name"])}</span>'
            f'{"".join(pills)}</div>'
            f'<div class="mm-proj">{html.escape(proj_label(r["project"]))}</div>'
            f'<div class="mm-desc">{html.escape(r["description"] or "(无描述)")}</div>',
            unsafe_allow_html=True,
        )

        with st.expander("正文 / 详情 / 维度"):
            st.code(r["path"], language=None)
            key = f"dim_{ctx}_{r['path']}"
            dc = st.columns(2)
            nat = dc[0].selectbox("性质 nature", list(C.ALL_NATURE),
                                  index=list(C.ALL_NATURE).index(r["nature"]) if r["nature"] in C.ALL_NATURE else 0,
                                  key="n_" + key)
            vol = dc[1].selectbox("波动性 volatility", list(C.ALL_VOLATILITY),
                                  index=list(C.ALL_VOLATILITY).index(r["volatility"]) if r["volatility"] in C.ALL_VOLATILITY else 1,
                                  key="v_" + key)
            if nat != r["nature"] or vol != r["volatility"]:
                if st.button("💾 保存维度", key="sd_" + key):
                    if nat != r["nature"]:
                        lifecycle.set_nature(c, r["path"], nat)
                    if vol != r["volatility"]:
                        lifecycle.set_volatility(c, r["path"], vol)
                    toast("维度已更新"); st.rerun()
            st.markdown(r["body"][:3000] or "_(空)_")
            if r["reason"]:
                st.info(f"最近降级原因: {r['reason']}")
            st.caption(f"confidence={r['confidence']} · access={r['access_count']} · "
                       f"last={r['last_accessed'] or '?'} · created={r['created_at'] or '?'}")

        # 操作
        cols = st.columns(6)
        key = f"{ctx}_{r['path']}"
        tier = r["tier"]
        if tier == C.STATUS_ACTIVE:
            if cols[0].button("📥 归档", key="ar_" + key, use_container_width=True):
                lifecycle.archive(c, r["path"]); toast("已归档"); st.rerun()
            if cols[1].button("🗑 回收", key="tr_" + key, use_container_width=True):
                lifecycle.trash(c, r["path"]); toast("已移入回收站"); st.rerun()
            if r["pinned"]:
                if cols[2].button("🔓 解锁", key="up_" + key, use_container_width=True):
                    lifecycle.set_pinned(c, r["path"], False); toast("已解锁"); st.rerun()
            else:
                if cols[2].button("🔒 锁定", key="pi_" + key, use_container_width=True):
                    lifecycle.set_pinned(c, r["path"], True); toast("已锁定"); st.rerun()
            if r["scope"] != "global":
                if cols[3].button("🌐 全局", key="pg_" + key, use_container_width=True):
                    lifecycle.promote_to_global(c, r["path"]); toast("已提升为全局记忆"); st.rerun()
            if r["nature"] == "todo":
                if cols[4].button("✅ 完成", key="td_" + key, use_container_width=True):
                    lifecycle.complete_todo(c, r["path"]); toast("TODO 完成, 已归档"); st.rerun()
        elif tier == C.STATUS_ARCHIVED:
            if cols[0].button("♻️ 还原", key="re_" + key, use_container_width=True):
                lifecycle.restore(c, r["path"]); toast("已还原到 active"); st.rerun()
            if cols[1].button("🗑 回收", key="tr_" + key, use_container_width=True):
                lifecycle.trash(c, r["path"]); toast("已移入回收站"); st.rerun()
        elif tier == C.STATUS_TRASH:
            if cols[0].button("♻️ 还原", key="ut_" + key, use_container_width=True):
                lifecycle.untrash(c, r["path"]); toast("已还原"); st.rerun()
            st.session_state.setdefault("_confirm_purge", set())
            if cols[1].button("❌ 永久删除", key="pu_" + key, use_container_width=True):
                st.session_state["_confirm_purge"].add(r["path"]); st.rerun()
            if r["path"] in st.session_state.get("_confirm_purge", set()):
                if cols[2].button("⚠️ 确认", key="cpu_" + key, use_container_width=True):
                    lifecycle.purge(c, r["path"])
                    st.session_state["_confirm_purge"].discard(r["path"])
                    toast("已物理删除(git 仍有兜底)"); st.rerun()


# ================= 页面 =====================================================
if page == "总览 Dashboard":
    c = con()
    s = index.stats(c)
    st.title("总览")
    m = st.columns(4)
    m[0].metric("记忆总数", s["total"])
    m[1].metric("🟢 活跃", s["by_tier"].get("active", 0))
    m[2].metric("🟡 归档", s["by_tier"].get("archived", 0))
    m[3].metric("⚪ 回收站", s["by_tier"].get("trash", 0))

    st.subheader("健康度")
    stale = lifecycle.stale_candidates(c)
    dupes = lifecycle.duplicate_pairs(c, max_pairs=100)
    todos = [r for r in index.all_rows(c, tier=C.STATUS_ACTIVE) if r["nature"] == "todo"]
    h = st.columns(4)
    h[0].metric("🔒 已锁定", s["pinned"])
    h[1].metric("⏳ 建议归档", len(stale))
    h[2].metric("📝 待办", len(todos))
    h[3].metric("👯 疑似重复", len(dupes))

    st.divider()
    col1, col2 = st.columns([3, 2])
    with col1:
        st.subheader("按项目分布")
        st.bar_chart({proj_label(k): v for k, v in s["by_project"].items()},
                     horizontal=True, color="#6c8cff")
    with col2:
        st.subheader("维度分布")
        st.caption("类型")
        st.markdown(pill_row(s["by_type"], kind_map={k: "type" for k in s["by_type"]}),
                    unsafe_allow_html=True)
        st.caption("性质 nature")
        st.markdown(pill_row(s.get("by_nature", {})), unsafe_allow_html=True)
        st.caption("波动性 volatility")
        st.markdown(pill_row(s.get("by_volatility", {})), unsafe_allow_html=True)
        st.caption("作用域 scope")
        st.markdown(pill_row(s["by_scope"]), unsafe_allow_html=True)


elif page == "待办 TODO":
    c = con()
    st.title("📝 待办 TODO")
    rows = [r for r in index.all_rows(c, tier=C.STATUS_ACTIVE) if r["nature"] == "todo"]
    st.caption(f"{len(rows)} 条待办。完成后点「✅ 完成」即归档(可还原)，不再污染日常召回。")
    for r in rows:
        render_card(r, ctx="todo")


elif page == "分类建议":
    c = con()
    st.title("分类建议")
    st.caption("基于标题/描述/正文关键词推断 nature 与 volatility。建议供参考，可逐条或批量采纳。")
    sugg = lifecycle.classify_all(c, apply=False)
    st.metric("待采纳建议", len(sugg))
    if sugg:
        with st.expander("⚙️ 批量采纳全部建议（先自动 git 快照）"):
            st.write("会逐条写入 frontmatter，每条可在操作日志里单独 undo。")
            if st.button("批量采纳", type="primary"):
                gitbackup.snapshot_commit("before classify apply (panel)")
                lifecycle.classify_all(c, apply=True)
                toast(f"已采纳 {len(sugg)} 条"); st.rerun()
        for i, sug in enumerate(sugg[:200]):
            with st.container(border=True):
                cur, sg = sug["current"], sug["suggest"]
                pills = []
                for k in ("nature", "volatility"):
                    if k in sg:
                        pills.append(pill(f"{cur.get(k)}→{sg[k]}", sg[k]))
                st.markdown(
                    f'<div class="mm-head"><span class="mm-name">{html.escape(sug["name"])}</span>'
                    f'{"".join(pills)}</div>', unsafe_allow_html=True)
                if st.button("采纳此条", key=f"sg_{i}_{sug['path']}"):
                    if "nature" in sg:
                        lifecycle.set_nature(c, sug["path"], sg["nature"])
                    if "volatility" in sg:
                        lifecycle.set_volatility(c, sug["path"], sg["volatility"])
                    toast("已采纳"); st.rerun()


elif page == "检索 / 浏览":
    c = con()
    st.title("检索 / 浏览")
    q = st.text_input("🔎 跨项目检索（留空=按过滤条件浏览）", "")
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
        st.caption(f"🎯 {len(hits)} 条结果")
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
        st.caption(f"{len(rows)} 条（显示前 {topk}）")
        for r in rows[:topk]:
            render_card(r, ctx="browse")


elif page == "归档 / 回收站":
    c = con()
    st.title("归档 / 回收站")
    tab1, tab2 = st.tabs(["📥 归档区", "🗑 回收站"])
    with tab1:
        rows = index.all_rows(c, tier=C.STATUS_ARCHIVED)
        st.caption(f"{len(rows)} 条归档。归档不丢，强命中会自动复活；也可手动还原。")
        for r in rows:
            render_card(r, ctx="arch")
    with tab2:
        rows = index.all_rows(c, tier=C.STATUS_TRASH)
        st.caption(f"{len(rows)} 条在回收站。保留期 {C.TRASH_RETENTION_DAYS} 天，永久删除需逐条确认。")
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
    st.title("去重工作台")
    thr = st.slider("相似度阈值", 0.70, 0.98, 0.82, 0.02)
    pairs = lifecycle.duplicate_pairs(c, threshold=thr)
    st.caption(f"{len(pairs)} 对疑似重复。合并 = 归档其中一条（保留另一条），原件进归档区可还原。")
    for i, p in enumerate(pairs):
        a, b = p["a"], p["b"]
        with st.container(border=True):
            st.markdown(f'<div class="mm-head">{pill(f"相似度 {p["ratio"]:.2f}", "type")}</div>',
                        unsafe_allow_html=True)
            cc = st.columns(2)
            for col, m, other, tag in [(cc[0], a, b, "A"), (cc[1], b, a, "B")]:
                with col:
                    st.markdown(
                        f'<div class="mm-name">{tag}: {html.escape(m["name"])}</div>'
                        f'<div class="mm-proj">{html.escape(proj_label(m["project"]))} · {m["type"]}</div>'
                        f'<div class="mm-desc">{html.escape((m["description"] or "")[:120])}</div>',
                        unsafe_allow_html=True)
                    if st.button(f"归档 {tag}（保留另一条）", key=f"dup_{i}_{tag}",
                                 use_container_width=True):
                        lifecycle.archive(c, m["path"], reason=f"去重: 与 {other['name']} 重复")
                        toast(f"已归档 {m['name']}"); st.rerun()


elif page == "操作日志":
    st.title("操作日志")
    st.caption("每步可撤销。撤销会逆向还原文件并重建索引。")
    log = ops.read_log(limit=80)
    if not log:
        st.info("暂无操作记录")
    for e in log:
        with st.container(border=True):
            cols = st.columns([4, 3, 2])
            with cols[0]:
                st.markdown(
                    f'<div class="mm-head">{pill(e["action"], "type")}'
                    f'<span class="mm-proj">{html.escape(e.get("note",""))}</span></div>',
                    unsafe_allow_html=True)
            cols[1].caption(f"{e['ts']} · {len(e['changes'])} 文件")
            if not e["action"].startswith("undo"):
                if cols[2].button("↩️ 撤销", key="undo_" + e["id"], use_container_width=True):
                    lifecycle.undo(con(), e["id"]); toast("已撤销"); st.rerun()
