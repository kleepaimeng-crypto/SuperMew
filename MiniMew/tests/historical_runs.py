"""历史 schema 的测试数据构造器；不让最新 ORM 冒充旧版运行协议。"""

import json
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import text

from minimew.conversations import title_from_content
from minimew.model_control import freeze_current_snapshot


async def event(session, rid, tid, kind, data, sequence):
    eid = uuid4()
    await session.execute(
        text("""INSERT INTO run_events
        (event_id,run_id,thread_id,sequence,type,timestamp,data)
        VALUES(:eid,:rid,:tid,:seq,:kind,clock_timestamp(),cast(:data AS jsonb))"""),
        dict(eid=eid, rid=rid, tid=tid, seq=sequence, kind=kind, data=json.dumps(data)),
    )
    await session.execute(text("INSERT INTO event_outbox(event_id) VALUES(:eid)"), dict(eid=eid))
    await session.execute(
        text("UPDATE runs SET last_event_sequence=:seq WHERE id=:rid"), dict(seq=sequence, rid=rid)
    )


async def reserve_legacy_run(session, user_id, thread_id, data, *, credentials_available=True):
    assert credentials_available
    async with session.begin():
        last = await session.scalar(
            text("SELECT last_sequence FROM threads WHERE id=:tid AND user_id=:uid FOR UPDATE"),
            dict(tid=thread_id, uid=user_id),
        )
        assert last is not None
        snapshot = await freeze_current_snapshot(session)
        rid, uid, aid = uuid4(), uuid4(), uuid4()
        await session.execute(
            text("""INSERT INTO runs(id,thread_id,user_id,idempotency_key,
            model_snapshot_json,model_catalog_hash)
            VALUES(:rid,:tid,:owner,:key,cast(:snap AS jsonb),:hash)"""),
            dict(
                rid=rid,
                tid=thread_id,
                owner=user_id,
                key=data.idempotency_key,
                snap=json.dumps(snapshot.model_dump(mode="json", exclude={"hitl"})),
                hash=snapshot.catalog_hash(),
            ),
        )
        for mid, role, content, sequence, status in [
            (uid, "user", data.content, last + 1, "completed"),
            (aid, "assistant", "", last + 2, "pending"),
        ]:
            await session.execute(
                text("""INSERT INTO messages(id,thread_id,run_id,sequence,role,status,content)
                VALUES(:mid,:tid,:rid,:seq,:role,:status,:content)"""),
                dict(
                    mid=mid,
                    tid=thread_id,
                    rid=rid,
                    seq=sequence,
                    role=role,
                    status=status,
                    content=content,
                ),
            )
        await session.execute(
            text(
                """UPDATE threads SET last_sequence=:seq,
                title=CASE WHEN last_sequence=0 THEN :title ELSE title END WHERE id=:tid"""
            ),
            dict(seq=last + 2, title=title_from_content(data.content), tid=thread_id),
        )
        await event(
            session,
            rid,
            thread_id,
            "run.created",
            dict(status="pending", assistant_message_id=str(aid)),
            1,
        )
    return SimpleNamespace(id=rid), True


async def finish_legacy_answer(sessions, rid, answer):
    async with sessions() as session, session.begin():
        tid = await session.scalar(text("SELECT thread_id FROM runs WHERE id=:rid"), dict(rid=rid))
        await session.execute(
            text("""UPDATE runs SET status='running',owner_token=:token,
            started_at=clock_timestamp(),lease_until=clock_timestamp()+interval '330 seconds'
            WHERE id=:rid"""),
            dict(token=uuid4(), rid=rid),
        )
        await event(session, rid, tid, "run.started", dict(status="running"), 2)
        aid = await session.scalar(
            text("SELECT id FROM messages WHERE run_id=:rid AND role='assistant'"), dict(rid=rid)
        )
        await session.execute(
            text("UPDATE messages SET status='completed',content=:content WHERE id=:aid"),
            dict(aid=aid, content=answer.content),
        )
        await session.execute(
            text("""INSERT INTO answer_materials(run_id,message_id,materials)
            VALUES(:rid,:aid,cast(:data AS jsonb))"""),
            dict(rid=rid, aid=aid, data=answer.materials.model_dump_json()),
        )
        await event(
            session,
            rid,
            tid,
            "message.completed",
            dict(message_id=str(aid), status="completed", content=answer.content),
            3,
        )
        await event(session, rid, tid, "run.completed", dict(status="succeeded"), 4)
        await session.execute(
            text("UPDATE runs SET status='succeeded',finished_at=clock_timestamp() WHERE id=:rid"),
            dict(rid=rid),
        )
