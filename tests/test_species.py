#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对象（物种）判定回归测试：判不出 → 纯细胞(无活体) 或 推定小鼠。

跑法：
  python tests/test_species.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import funnel  # noqa: E402

CASES = [
    # (标题, 摘要片段, 期望 species, 说明)
    ("GWAS of backfat thickness in pigs", "A total of 1200 pigs were genotyped.", ["猪"], "明确猪"),
    ("Immune response of guinea pig", "Guinea pig was used as the model.", ["其他动物"], "豚鼠≠猪"),
    ("C2C12 myoblast differentiation", "Cells were cultured in vitro and transfected with siRNA.", ["纯细胞(无活体)"], "只有细胞信号"),
    ("Regulation of adipogenesis", "In vitro, 3T3-L1 cells were induced; in vivo, mice were fed HFD.", ["小鼠"], "细胞+活体，物种明确"),
    ("Privacy-preserving pangenome graphs", "We present a graph-based index for pangenome queries.", ["小鼠(推定)"], "无任何生物学物种信号"),
    ("Feeding trial on growth performance", "Animals were housed and fed for 42 days before slaughter.", ["小鼠(推定)"], "有活体信号但无物种词"),
    ("Transcriptome analysis of bovine muscle", "Longissimus muscle samples from cattle were collected.", ["牛"], "明确牛"),
]


def run():
    ok = bad = 0
    for title, ab, want, note in CASES:
        r = {"title": title, "abstract": ab}
        got = funnel.detect_species(r)
        mark = "PASS" if got == want else "FAIL"
        ok, bad = (ok + 1, bad) if mark == "PASS" else (ok, bad + 1)
        inferred = r.get("species_inferred", False)
        print(f"[{mark}] {note:16s} 期望={want} 实际={got} 推定标记={inferred}")
    print(f"\n合计 {ok} 通过 / {bad} 失败")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
