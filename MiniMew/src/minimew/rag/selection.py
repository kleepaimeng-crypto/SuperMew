"""v4按原问题/子问题轮流选组；只认来源身份，不把E编号或分数当覆盖证明。"""

from minimew.rag.merging import select
from minimew.rag.protocol import MaterialGroup, RetrievalResult


def balanced(pools, preferred, limit, trace):
    queues = [list(pool.groups) for pool in pools]
    selected, seen, sections, representatives = [], set(), set(), []

    def offer(group, quota=None):
        members = tuple(c for c in group.members if c.key not in seen)
        if not members:
            return
        keys = {c.key for c in members}
        deduplicated = MaterialGroup(
            members,
            group.completed and len(members) == len(group.members),
            tuple(s for s in group.seeds if s.key in keys),
        )
        room = limit - len(seen)
        chosen = select((deduplicated,), min(room, quota) if quota is not None else room, trace)
        for value in chosen:
            selected.append(value)
            for candidate in value.members:
                seen.add(candidate.key)
                sections.add((candidate.key[0], candidate.row["payload"]["section_id"]))

    # 每个池先保留一个有来源身份的代表组；已共享的同组可以代表多个池。
    for pool_index, (queue, supported) in enumerate(zip(queues, preferred, strict=True)):
        if not queue:
            representatives.append(frozenset())
            continue
        index = next(
            (i for i, g in enumerate(queue) if any(c.key in supported for c in g.members)), 0
        )
        group = queue.pop(index)
        representatives.append(frozenset(c.key for c in group.members))
        reserved = sum(bool(q) for q in queues[pool_index + 1 :])
        offer(group, max(0, limit - len(seen) - reserved))
        if any(c.key not in seen for c in group.members):
            queue.insert(0, group)  # 代表轮次后仍有余量时，可继续补全该原组。

    while any(queues) and len(seen) < limit:
        for queue in queues:
            if not queue:
                continue
            index = next(
                (
                    i
                    for i, group in enumerate(queue)
                    if any(
                        c.key not in seen
                        and (c.key[0], c.row["payload"]["section_id"]) not in sections
                        for c in group.members
                    )
                ),
                0,
            )
            offer(queue.pop(index))
    trace.stages["aggregation"] = "representatives-v1"
    if any(c.key not in seen for queue in queues for g in queue for c in g.members):
        trace.limited = True
    return RetrievalResult(tuple(selected), trace), tuple(representatives)
