"""各路全局名次的单一 RRF；来源身份去重，分数不冒充概率。"""

from dataclasses import replace

from minimew.rag.protocol import Candidate


def fuse(dense, lexical, policy):
    merged = {}
    for route, rows in (("dense", dense), ("bm25", lexical)):
        seen = set()
        ordered = sorted((Candidate(r) for r in rows), key=lambda c: (-c.row["score"], c.key))
        rank = 0
        for item in ordered:
            if item.key in seen:
                continue
            seen.add(item.key)
            rank += 1
            old = merged.get(item.key, item)
            merged[item.key] = replace(
                old, **{route: item.row["score"]}, rrf=(old.rrf or 0) + 1 / (policy.rrf_k + rank)
            )
    return tuple(sorted(merged.values(), key=lambda c: (-c.rrf, c.key))[: policy.fused_k])
