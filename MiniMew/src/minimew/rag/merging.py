"""仅补全同一可信Section下的原片段；不递归提升成父全文。"""

from minimew.rag.protocol import Candidate, MaterialGroup


def section_key(candidate):
    p = candidate.row["payload"]
    return (*candidate.key[:2], str(p["section_id"]))


async def complete(candidates, scope, runtime, policy, trace):
    groups = []
    consumed = set()
    total = len(candidates)
    for seed in candidates:
        if seed.key in consumed:
            continue
        key = section_key(seed)
        seeds = tuple(c for c in candidates if section_key(c) == key)
        directory = next(
            (
                d
                for d in scope.directory
                if str(d.identity.version_id) == key[0] and d.build_fingerprint == key[1]
            ),
            None,
        )
        children = (
            tuple(c for c in directory.chunks if str(c.section_id) == key[2]) if directory else ()
        )
        section = (
            next((s for s in directory.sections if str(s.section_id) == key[2]), None)
            if directory
            else None
        )
        eligible = (
            policy.merge
            and section is not None
            and section.parent_section_id is not None
            and len(seeds) >= 2
            and len(seeds) * 2 > len(children)
        )
        if (
            eligible
            and len(children) <= policy.group_limit
            and total + len(children) - len(seeds) <= policy.candidate_limit
        ):
            manifest = next(
                m
                for m in scope.targets
                if m.scope.identity == directory.identity
                and m.scope.build_fingerprint == directory.build_fingerprint
            )
            missing = [
                str(c.chunk_id)
                for c in children
                if str(c.chunk_id) not in {s.row["chunk_id"] for s in seeds}
            ]
            extra = await runtime.sync(runtime.index.fetch, manifest, missing) if missing else []
            lookup = {s.row["chunk_id"]: s for s in seeds}
            lookup.update({r["chunk_id"]: Candidate(r, rrf=0.0) for r in extra})
            members = tuple(
                lookup[str(c.chunk_id)] for c in sorted(children, key=lambda c: c.ordinal)
            )
            groups.append(MaterialGroup(members, True, seeds))
            total += len(missing)
            consumed.update(s.key for s in seeds)
        else:
            if eligible:
                trace.limited = True
            groups.append(MaterialGroup((seed,)))
            consumed.add(seed.key)
    trace.stages["completed_groups"] = sum(g.completed for g in groups)
    trace.stages["candidate_pairs"] = sum(len(g.members) for g in groups)
    return tuple(groups)


def select(groups, limit, trace):
    selected = []
    used = 0
    for group in groups:
        if used + len(group.members) <= limit:
            selected.append(group)
            used += len(group.members)
        else:
            trace.limited = True
            for seed in group.seeds:
                if used >= limit:
                    break
                selected.append(MaterialGroup((seed,)))
                used += 1
    return tuple(selected)
