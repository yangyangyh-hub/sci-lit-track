#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成最终报告。

结构：
  # <日期> 文献进展
  ## 一、精选导读        ← 相关性与影响因子综合最高的若干篇
  ## 二 ~ 六、五个主题    ← 各自收录的文章（元数据 + 直达链接 + 四段概述）
其余（漏斗统计、口径说明）一律不写。

用法：
  python build_report.py --run <运行目录> --content <内容目录> [--out 指定文件] [--outdir 目录]
"""
from __future__ import annotations

import argparse
import glob
import json
import os

SEG = [("bg", "背景"), ("method", "方法"), ("result", "主要结果"), ("conclusion", "结论")]

# 报告投递目录：优先 --outdir，其次环境变量 SCI_RADAR_OUTDIR，最后当前目录下的 reports/
OUTDIR = os.environ.get("SCI_RADAR_OUTDIR", os.path.join(os.getcwd(), "reports"))
CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八"]


def load_content(d):
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        for k, v in json.load(open(p, encoding="utf-8")).items():
            out.setdefault(str(k), []).extend(v)
    return out


def link_of(r: dict) -> str:
    doi = (r.get("doi") or "").strip()
    if doi:
        return f"https://doi.org/{doi}"
    pmid = (r.get("pmid") or "").strip()
    if pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    rid = str(r.get("id") or "")
    if rid.startswith("PMC"):
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{rid}/"
    if rid:
        return f"https://europepmc.org/article/{r.get('src_db') or 'MED'}/{rid}"
    return ""


def pick_featured(res, top_pool=15, n=5):
    """精选：先按相关性取前 top_pool，再按影响因子取前 n。"""
    rows = [(k, r) for k, v in res["final"].items() for r in v["picked"]]
    rows.sort(key=lambda kv: -(kv[1].get("rel") or 0))
    pool = rows[:top_pool]
    pool.sort(key=lambda kv: -((kv[1].get("impact") or 0)))
    return pool[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--content", required=True)
    ap.add_argument("--out", default=None, help="省略时写入 OUTDIR/<日期>-文献进展.md")
    ap.add_argument("--outdir", default=OUTDIR, help="报告投递目录")
    ap.add_argument("--featured", type=int, default=5)
    a = ap.parse_args()

    res = json.load(open(os.path.join(a.run, "result.json"), encoding="utf-8"))
    content = load_content(a.content)
    title_date = res["meta"]["window"][1]

    def cmap(k):
        return {(c.get("doi") or "").lower(): c for c in content.get(k, [])}

    L = [f"# {title_date} 文献进展", ""]

    # 一、精选导读
    feat = pick_featured(res, n=a.featured)
    L += ["## 一、精选导读", ""]
    for i, (k, r) in enumerate(feat, 1):
        t = cmap(k).get((r.get("doi") or "").lower(), {}).get("title_cn") or r.get("title")
        t = " ".join(str(t).split())
        meta = "｜".join([x for x in [r.get("journal"), r.get("zone"),
                                      (f"IF {r.get('impact')}" if r.get("impact") is not None else None)] if x])
        lnk = link_of(r)
        L.append(f"{i}. **{t}**")
        L.append(f"   {meta}" + (f"　[原文链接]({lnk})" if lnk else ""))
        L.append("")

    # 二~六、五个主题
    for idx, k in enumerate(sorted(res["final"]), start=1):
        f = res["final"][k]
        L += [f"## {CN_NUM[idx]}、{f['name']}", ""]
        if not f["picked"]:
            L += ["> 本期无符合期刊门槛与时间窗口的文献。", ""]
            continue
        for i, r in enumerate(f["picked"], 1):
            doi = (r.get("doi") or "").lower()
            c = cmap(k).get(doi, {})
            raw_t = c.get("title_cn") or r.get("title") or ""
            L += [f"### {i}. {' '.join(str(raw_t).split())}", ""]
            en = " ".join(str(r.get("title") or "").split())
            if en:
                L += [f"- **英文原题**：{en}", ""]
            meta = [
                f"**期刊**：{r.get('journal') or '—'}",
                f"**分区**：{r.get('zone') or '—'}",
                f"**影响因子**：{r.get('impact') if r.get('impact') is not None else '—'}",
                f"**首次在线**：{r.get('date_online') or '—'}",
                f"**物种**：{'/'.join(r.get('species') or [])}",
            ]
            L += ["- " + "｜".join(meta), ""]
            lnk = link_of(r)
            if lnk:
                L += [f"- **链接**：[{lnk}]({lnk})", ""]
            if doi:
                L += [f"- **DOI**：{doi}", ""]
            for kk, n in SEG:
                L += [f"**{n}**：{(c.get(kk) or '摘要未提供').strip()}", ""]
            L += ["---", ""]

    md = "\n".join(L)
    out_path = a.out
    if not out_path:
        os.makedirs(a.outdir, exist_ok=True)
        out_path = os.path.join(a.outdir, f"{res['meta']['window'][1]}-文献进展.md")
    open(out_path, "w", encoding="utf-8").write(md)
    total = sum(len(v["picked"]) for v in res["final"].values())
    print(f"写出 {out_path}（{len(md)} 字符，{total} 篇，精选 {len(feat)} 篇）")


if __name__ == "__main__":
    main()
