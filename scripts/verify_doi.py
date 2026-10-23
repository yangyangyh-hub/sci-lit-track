#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DOI 核验：对最终收录的每篇，用 Crossref 反查该 DOI 的题名并与本地题名比对。

发现 2026-10-23：「Expanding the pig metabolomics QTL catalog」被挂上了 2003 年 BBRC 的 DOI。
根因是多源合并时 DOI 与题名不是同一条记录。本脚本只对收录篇做核验（约 50 次请求），
命中不一致时：把真实 DOI 写回 `doi_fixed`，并把 `doi` 置空、`doi_status` 标 `mismatch`。

用法：
  python verify_doi.py --run runs/20261023_r3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

MAILTO = os.environ.get("LIT_MAILTO", "anonymous@example.com")
UA = {"User-Agent": f"lit-track-verify/1.0 (mailto:{MAILTO})"}
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def toks(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (s or "").lower())}


def sim(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def get(url: str, timeout=30):
    for i in range(3):
        try:
            return json.loads(OP.open(urllib.request.Request(url, headers=UA), timeout=timeout).read().decode())
        except Exception:  # noqa: BLE001
            time.sleep(1.5 * (i + 1))
    return None


def crossref_by_doi(doi: str):
    d = get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}?mailto={MAILTO}")
    if not d:
        return None, ""
    m = d.get("message") or {}
    return m, ((m.get("title") or [""])[0] or "")


def crossref_search(title: str):
    q = urllib.parse.urlencode({"query.bibliographic": title[:220], "rows": 3,
                                "select": "DOI,title", "mailto": MAILTO})
    d = get("https://api.crossref.org/works?" + q)
    if not d:
        return []
    return (d.get("message") or {}).get("items") or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--threshold", type=float, default=0.5)
    a = ap.parse_args()
    p = os.path.join(a.run, "result.json")
    res = json.load(open(p, encoding="utf-8"))

    stat = {"ok": 0, "unverified": 0, "mismatch": 0, "fixed": 0}
    for k, v in res["final"].items():
        for r in v["picked"]:
            doi = (r.get("doi") or "").lower()
            local = toks(r.get("title"))
            if not doi:
                r["doi_status"] = "missing"
                continue
            meta, cr_title = crossref_by_doi(doi)
            if meta is None:
                r["doi_status"] = "unverified(Crossref 无响应或查无此 DOI)"
                stat["unverified"] += 1
                continue
            s = sim(local, toks(cr_title))
            if s >= a.threshold:
                r["doi_status"] = "ok"
                r["doi_cr_title"] = cr_title
                stat["ok"] += 1
                continue
            # DOI 与题名对不上：按题名反查真 DOI
            r["doi_status"] = "mismatch"
            r["doi_wrong"] = doi
            r["doi_cr_title"] = cr_title
            fixed = ""
            for it in crossref_search(r.get("title") or ""):
                if sim(local, toks((it.get("title") or [""])[0])) >= 0.75:
                    fixed = (it.get("DOI") or "").lower()
                    break
            if fixed:
                r["doi_fixed"] = fixed
                stat["fixed"] += 1
            stat["mismatch"] += 1
            time.sleep(0.3)
        time.sleep(0.2)

    json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("核验结果：", stat)
    bad = [(k, r.get("title"), r.get("doi_wrong"), r.get("doi_fixed"))
           for k, v in res["final"].items() for r in v["picked"] if r.get("doi_status") == "mismatch"]
    for b in bad:
        print("  ✗", b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
