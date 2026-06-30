"""命令行入口。

用法示例:
  python -m memmgr scan                      # 重建索引
  python -m memmgr stats                      # 总览
  python -m memmgr search "MCP windows"       # 检索
  python -m memmgr recall <project> "查询"     # 模拟某项目的召回(hook 用)
  python -m memmgr archive <path>             # 归档某条
  python -m memmgr restore <path>             # 还原
  python -m memmgr trash <path>               # 移入回收站
  python -m memmgr pin <path> / unpin <path>
  python -m memmgr promote <path>             # 提升为全局
  python -m memmgr dupes                       # 列疑似重复对
  python -m memmgr stale                       # 列建议归档项
  python -m memmgr undo [op_id]                # 撤销
  python -m memmgr log                         # 看操作日志
  python -m memmgr snapshot "msg"              # 手动 git 快照
  python -m memmgr panel                       # 启动可视化面板
"""

from __future__ import annotations

import argparse
import sys

from . import config as C
from . import index, lifecycle, retrieval, ops, gitbackup, status


def _con():
    return index.connect()


def cmd_scan(args):
    con = _con()
    st = index.rebuild(con)
    print(f"已重建索引: active={st['active']} archived={st['archived']} "
          f"trash={st['trash']} errors={len(st['errors'])}")
    for e in st["errors"][:10]:
        print("  ERR", e)


def cmd_status(args):
    con = _con()
    status.run(con,
               as_json=getattr(args, "json", False),
               watch=getattr(args, "watch", 0) or 0,
               full=not getattr(args, "fast", False))


def cmd_stats(args):
    con = _con()
    s = index.stats(con)
    print(f"记忆总数: {s['total']}  (active={s['by_tier'].get('active',0)} "
          f"archived={s['by_tier'].get('archived',0)} trash={s['by_tier'].get('trash',0)})")
    print(f"锁定(pinned): {s['pinned']}")
    print("\n按项目(active):")
    for p, n in s["by_project"].items():
        print(f"  {n:5}  {C.decode_project_id(p)}")
    print(f"\n按类型: {s['by_type']}")
    print(f"按作用域: {s['by_scope']}")
    stale = lifecycle.stale_candidates(con)
    dupes = lifecycle.duplicate_pairs(con, max_pairs=50)
    print(f"\n健康度: 建议归档(久未命中) {len(stale)} 条; 疑似重复 {len(dupes)} 对")


def cmd_search(args):
    con = _con()
    tiers = [args.tier] if args.tier else None
    hits = retrieval.search(con, args.query, top_k=args.k, tiers=tiers)
    if not hits:
        print("(无结果)")
        return
    for h in hits:
        r = h.row
        pin = "🔒" if r["pinned"] else "  "
        print(f"[{h.score:6.1f} {h.why:4}] {pin} {r['name']:<38.38} "
              f"| {C.decode_project_id(r['project'])}")
        print(f"            {r['description'][:80]}")
        print(f"            {r['path']}")


def cmd_recall(args):
    con = _con()
    hits = retrieval.recall_for_project(con, args.project, args.query, top_k=args.k)
    for h in hits:
        print(f"- {h.row['name']}: {h.row['description']}")


def _op(args, fn, *extra):
    con = _con()
    op_id = fn(con, args.path, *extra)
    print(f"完成: {fn.__name__} -> op {op_id}")


def cmd_archive(args):
    _op(args, lifecycle.archive)


def cmd_restore(args):
    _op(args, lifecycle.restore)


def cmd_trash(args):
    _op(args, lifecycle.trash)


def cmd_purge(args):
    con = _con()
    print(f"完成 purge -> {lifecycle.purge(con, args.path)}")


def cmd_pin(args):
    con = _con()
    print(lifecycle.set_pinned(con, args.path, True))


def cmd_unpin(args):
    con = _con()
    print(lifecycle.set_pinned(con, args.path, False))


def cmd_promote(args):
    con = _con()
    print(f"提升为全局 -> {lifecycle.promote_to_global(con, args.path)}")


def cmd_dupes(args):
    con = _con()
    pairs = lifecycle.duplicate_pairs(con)
    print(f"疑似重复 {len(pairs)} 对:")
    for p in pairs[:args.k]:
        print(f"  [{p['ratio']:.2f}] {p['a']['name']}  <->  {p['b']['name']}")
        print(f"          {p['a']['project']} | {p['b']['project']}")


def cmd_stale(args):
    con = _con()
    rows = lifecycle.stale_candidates(con)
    print(f"建议归档(超 TTL 未命中, 未锁定; stable 免疫) {len(rows)} 条:")
    for r in rows[:args.k]:
        print(f"  [{r['volatility']:8}] {r['name']:<36.36} "
              f"last={r['last_accessed'] or r['created_at'] or '?'} "
              f"| {C.decode_project_id(r['project'])}")


def cmd_volatility(args):
    con = _con()
    print(lifecycle.set_volatility(con, args.path, args.value))


def cmd_nature(args):
    con = _con()
    print(lifecycle.set_nature(con, args.path, args.value))


def cmd_todos(args):
    con = _con()
    rows = [r for r in index.all_rows(con, tier=C.STATUS_ACTIVE)
            if r["nature"] == C.NAT_TODO]
    print(f"待办(todo) {len(rows)} 条:")
    for r in rows[:args.k]:
        print(f"  {r['name']:<40.40} | {C.decode_project_id(r['project'])}")
        print(f"      {r['description'][:80]}")


def cmd_classify(args):
    con = _con()
    if args.apply:
        gitbackup.snapshot_commit("before classify --apply")
    sugg = lifecycle.classify_all(con, apply=args.apply)
    verb = "已写入" if args.apply else "建议(加 --apply 写入)"
    print(f"{verb} {len(sugg)} 条分类:")
    for s in sugg[:args.k]:
        print(f"  {s['name']:<40.40} {s['current']} -> {s['suggest']}")


def cmd_undo(args):
    con = _con()
    entry = lifecycle.undo(con, args.op_id)
    if entry:
        print(f"已撤销: {entry['action']} ({entry['id']}) — {len(entry['changes'])} 个文件")
    else:
        print("没有可撤销的操作")


def cmd_log(args):
    for e in ops.read_log(limit=args.k):
        print(f"{e['ts']}  {e['action']:<18}  {len(e['changes'])}文件  "
              f"{e.get('note','')}  [{e['id']}]")


def cmd_snapshot(args):
    h = gitbackup.snapshot_commit(args.message)
    print(f"git 快照: {h or '(无变更或 git 不可用)'}")


def cmd_snapshots(args):
    snaps = gitbackup.list_snapshots(limit=args.k)
    if not snaps:
        print("(暂无快照; 用 memmgr snapshot \"说明\" 打一个)")
        return
    print(f"快照历史(最新在前) {len(snaps)} 个:")
    for s in snaps:
        print(f"  {s['hash']}  {s['date']}  {s['msg']}")


def cmd_restore_snapshot(args):
    con = _con()
    dry = not args.apply
    res = gitbackup.restore_snapshot(ref=args.ref, dry_run=dry)
    if res.get("error"):
        print("错误:", res["error"]); return
    ch = res["changed"]
    verb = "将还原(dry-run, 加 --apply 执行)" if dry else "已还原"
    print(f"快照 {res['ref']} 含 {res['snapshot_files']} 个文件; {verb} {len(ch)} 处变更:")
    for c in ch[:args.k]:
        print(f"  [{c['kind']:9}] {c['path']}")
    if len(ch) > args.k:
        print(f"  … 其余 {len(ch)-args.k} 处")
    if not dry:
        index.rebuild(con)
        print("索引已重建。")


def cmd_panel(args):
    import subprocess
    from pathlib import Path
    app = Path(__file__).parent.parent / "panel" / "app.py"
    print(f"启动面板: streamlit run {app}")
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app)])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memmgr", description="跨项目 Claude 记忆管理")
    # 裸 `memmgr` 默认显示 status(像 abtop 一跑就看全局)
    p.set_defaults(func=cmd_status, json=False, watch=0, fast=False)
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("status", help="终端健康仪表盘(默认命令)")
    sp.add_argument("--json", action="store_true", help="机器可读输出")
    sp.add_argument("--watch", type=int, default=0, metavar="N", help="每 N 秒刷新")
    sp.add_argument("--fast", action="store_true", help="跳过昂贵的重复检测")
    sp.set_defaults(func=cmd_status)

    sub.add_parser("scan").set_defaults(func=cmd_scan)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    sp = sub.add_parser("search"); sp.add_argument("query"); sp.add_argument("-k", type=int, default=10)
    sp.add_argument("--tier", choices=C.ALL_STATUSES); sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("recall"); sp.add_argument("project"); sp.add_argument("query")
    sp.add_argument("-k", type=int, default=8); sp.set_defaults(func=cmd_recall)

    for name, fn in [("archive", cmd_archive), ("restore", cmd_restore),
                     ("trash", cmd_trash), ("purge", cmd_purge),
                     ("pin", cmd_pin), ("unpin", cmd_unpin), ("promote", cmd_promote)]:
        sp = sub.add_parser(name); sp.add_argument("path"); sp.set_defaults(func=fn)

    sp = sub.add_parser("dupes"); sp.add_argument("-k", type=int, default=30); sp.set_defaults(func=cmd_dupes)
    sp = sub.add_parser("stale"); sp.add_argument("-k", type=int, default=30); sp.set_defaults(func=cmd_stale)

    sp = sub.add_parser("volatility"); sp.add_argument("path")
    sp.add_argument("value", choices=C.ALL_VOLATILITY); sp.set_defaults(func=cmd_volatility)
    sp = sub.add_parser("nature"); sp.add_argument("path")
    sp.add_argument("value", choices=C.ALL_NATURE); sp.set_defaults(func=cmd_nature)
    sp = sub.add_parser("todos"); sp.add_argument("-k", type=int, default=50); sp.set_defaults(func=cmd_todos)
    sp = sub.add_parser("classify"); sp.add_argument("--apply", action="store_true")
    sp.add_argument("-k", type=int, default=40); sp.set_defaults(func=cmd_classify)
    sp = sub.add_parser("undo"); sp.add_argument("op_id", nargs="?"); sp.set_defaults(func=cmd_undo)
    sp = sub.add_parser("log"); sp.add_argument("-k", type=int, default=30); sp.set_defaults(func=cmd_log)
    sp = sub.add_parser("snapshot"); sp.add_argument("message"); sp.set_defaults(func=cmd_snapshot)
    sp = sub.add_parser("snapshots"); sp.add_argument("-k", type=int, default=30); sp.set_defaults(func=cmd_snapshots)
    sp = sub.add_parser("restore-snapshot")
    sp.add_argument("ref", nargs="?", default="HEAD", help="快照 ref(默认 HEAD 最新); 用 snapshots 查历史哈希")
    sp.add_argument("--apply", action="store_true", help="真正写回(缺省只 dry-run 预览)")
    sp.add_argument("-k", type=int, default=40)
    sp.set_defaults(func=cmd_restore_snapshot)
    sub.add_parser("panel").set_defaults(func=cmd_panel)
    return p


def main(argv=None):
    # Windows 控制台 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
