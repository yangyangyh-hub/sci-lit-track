#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出版商黑名单回归测试（不计入缓存，跨轮复用）。

跑法：
  python tests/test_banned.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import funnel  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
cfg = json.load(open(os.path.join(ROOT, "config", "directions.animal-science.json"), encoding="utf-8"))
try:
    funnel.load_tables(cfg)
except Exception as e:
    print(f"[warn] 未加载分区表（{type(e).__name__}），仅测试刊名规则")
HAS_PUB = bool(funnel.PUB)
B = cfg["exclude_publishers"]

CASES = [
    # (journal, issns, 期望被排除)
    ("Animals", ["20762615"], True),
    ("Animals : an open access journal from MDPI", ["20762615"], True),
    ("Frontiers in Oncology", ["2234943X"], True),
    ("Frontiers in Veterinary Science", [], True),          # 无 ISSN → 前缀规则
    ("Frontiers in Microbiology", ["1664302X"], True),
    ("Scientific Reports", ["20452322"], True),
    ("Scientific reports", [], True),                       # 无 ISSN → 刊名规则
    ("Nature", ["00280836"], False),
    ("Nature Communications", ["20411723"], False),
    ("Journal of Dairy Science", ["00220302"], False),
    ("Animal Genetics", ["02689146"], False),
    ("International Journal of Molecular Sciences", ["14220067"], True),
]


def case_needs_pub(issns):
    """只有靠出版机构才能判定的用例（MDPI/Frontiers 全刊）需要索引。"""
    return bool(issns)


def run():
    ok = bad = 0
    for jn, issns, want in CASES:
        r = {"journal": jn, "issns": issns}
        if case_needs_pub(issns) and not HAS_PUB:
            print(f"[SKIP] {jn[:46]:46s} 需要出版机构索引，当前未加载分区表")
            continue
        why = funnel.banned_reason(r, B)
        got = why is not None
        mark = "PASS" if got == want else "FAIL"
        if mark == "PASS":
            ok += 1
        else:
            bad += 1
        print(f"[{mark}] {jn[:46]:46s} 期望排除={want!s:5s} 实际={why or '—'}  (出版机构={r.get('publisher') or '未知'})")
    print(f"\n合计 {ok} 通过 / {bad} 失败")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
