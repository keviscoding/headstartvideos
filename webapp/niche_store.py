"""Durable discovery frontier, cached evidence and leases for ephemeral workers."""
from __future__ import annotations

import hashlib
import json
import time

from webapp import database as db


class NicheStore:
    def __init__(self, job_id, owner):
        self.job_id, self.owner = job_id, owner
        self.ensure_schema()

    @staticmethod
    def ensure_schema():
        pk = "BIGSERIAL PRIMARY KEY" if db.IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
        with db._conn() as conn:
            cur = conn.cursor()
            for sql in [
                """CREATE TABLE IF NOT EXISTS niche_discovery_lease (
                    lease_key TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner TEXT NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL)""",
                f"""CREATE TABLE IF NOT EXISTS niche_discovery_tasks (
                    id {pk}, job_id TEXT NOT NULL, task_key TEXT NOT NULL,
                    kind TEXT NOT NULL, source TEXT NOT NULL, depth INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                    owner TEXT NOT NULL DEFAULT '', lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0, result_json TEXT NOT NULL DEFAULT '{{}}',
                    created_at DOUBLE PRECISION NOT NULL, finished_at DOUBLE PRECISION NOT NULL DEFAULT 0,
                    UNIQUE(job_id, task_key))""",
                """CREATE TABLE IF NOT EXISTS niche_discovery_cache (
                    channel_id TEXT PRIMARY KEY, signature TEXT NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL, result_json TEXT NOT NULL,
                    checked_at DOUBLE PRECISION NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS niche_discovery_budgets (
                    day TEXT NOT NULL, provider TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, provider))""",
                "CREATE INDEX IF NOT EXISTS idx_niche_tasks_queue ON niche_discovery_tasks (job_id, state, kind, source, id)",
                "CREATE INDEX IF NOT EXISTS idx_niche_cache_expiry ON niche_discovery_cache (expires_at)",
            ]:
                cur.execute(sql)
            for name, definition in [
                ("production_format", "TEXT DEFAULT ''"),
                ("avatar_confidence", "TEXT DEFAULT 'unknown'"),
                ("quality_status", "TEXT DEFAULT ''"),
                ("quality_evidence_json", "TEXT DEFAULT '{}'"),
                ("discovery_job_id", "TEXT DEFAULT ''"),
            ]:
                db._ensure_column(cur, "niche_channels", name, definition)

    def claim_run(self, ttl=180):
        now = time.time()
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute(db._q("""INSERT INTO niche_discovery_lease (lease_key, job_id, owner, expires_at)
                VALUES ('catalog', ?, ?, ?)
                ON CONFLICT(lease_key) DO UPDATE SET job_id=excluded.job_id,
                    owner=excluded.owner, expires_at=excluded.expires_at
                WHERE niche_discovery_lease.expires_at < ?
                   OR (niche_discovery_lease.job_id=excluded.job_id
                       AND niche_discovery_lease.owner=excluded.owner)
                RETURNING job_id"""), (self.job_id, self.owner, now+ttl, now))
            return cur.fetchone() is not None

    def heartbeat(self, meta=None):
        if not self.claim_run():
            return False
        run = db.get_niche_hunt_run_by_job_id(self.job_id)
        if not run or run["status"] != "running":
            return False
        if meta is not None:
            with db._conn() as conn:
                added=int(meta.get("added",0))
                conn.cursor().execute(db._q("""UPDATE niche_hunt_runs SET meta_json=?,
                    channels_upserted=CASE WHEN channels_upserted>? THEN channels_upserted ELSE ? END
                    WHERE job_id=? AND status='running'"""),
                    (json.dumps({**meta, "heartbeat_at": time.time()}),added,added,self.job_id))
        return True

    def reserve_budget(self, provider, amount, daily_cap):
        """Reserve conservative daily blocks, including abandoned work after a crash."""
        if not 0 < amount <= daily_cap:
            return False
        from datetime import datetime, timezone
        day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with db._conn() as conn:
            cur=conn.cursor()
            cur.execute(db._q("""INSERT INTO niche_discovery_budgets(day,provider,used) VALUES(?,?,?)
                ON CONFLICT(day,provider) DO UPDATE SET used=niche_discovery_budgets.used+excluded.used
                WHERE niche_discovery_budgets.used+excluded.used<=? RETURNING used"""),
                (day,provider,amount,daily_cap))
            return cur.fetchone() is not None

    def release_run(self):
        with db._conn() as conn:
            conn.cursor().execute(db._q("DELETE FROM niche_discovery_lease WHERE job_id=? AND owner=?"),
                                  (self.job_id, self.owner))

    def enqueue(self, kind, payload, *, source, depth=0):
        return bool(self.enqueue_many([(kind,payload,source,depth)]))

    def enqueue_many(self, entries):
        """Deduplicate a frontier batch with one connection/transaction."""
        rows=[]
        for kind,payload,source,depth in entries:
            identity = payload.get("channel_id") if kind == "channel" else payload.get("video_id") if kind == "related" else str(payload.get("query", "")).strip().casefold()
            if kind=="search" and payload.get("upload_month") and identity:
                identity += ":upload_month"
            if not identity: continue
            key=kind+":"+hashlib.sha256(identity.encode()).hexdigest()
            rows.append((self.job_id,key,kind,source,depth,json.dumps(payload),time.time()))
        if not rows: return 0
        with db._conn() as conn:
            cur=conn.cursor()
            cur.executemany(db._q("""INSERT INTO niche_discovery_tasks
                (job_id,task_key,kind,source,depth,payload_json,created_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(job_id,task_key) DO NOTHING"""),rows)
            return cur.rowcount

    def claim_tasks(self, kind, *, source=None, limit=1, lease_seconds=300):
        now = time.time()
        with db._conn() as conn:
            cur = conn.cursor()
            where = "job_id=? AND kind=? AND attempts < 3 AND (state='queued' OR (state='working' AND lease_until < ?))"
            params = [self.job_id,kind,now]
            if source:
                where += " AND source=?"
                params.append(source)
            lock = " FOR UPDATE SKIP LOCKED" if db.IS_PG else ""
            if not db.IS_PG:
                cur.execute("BEGIN IMMEDIATE")
            cur.execute(db._q(f"SELECT * FROM niche_discovery_tasks WHERE {where} ORDER BY depth,id LIMIT ?{lock}"),
                        tuple(params+[limit]))
            rows = [dict(r) for r in cur.fetchall()]
            for row in rows:
                cur.execute(db._q("UPDATE niche_discovery_tasks SET state='working',owner=?,lease_until=?,attempts=attempts+1 WHERE id=?"),
                            (self.owner,now+lease_seconds,row["id"]))
                row["payload"] = json.loads(row["payload_json"])
            return rows

    def finish_task(self, task, result, *, error=False):
        with db._conn() as conn:
            conn.cursor().execute(db._q("""UPDATE niche_discovery_tasks SET state=?,result_json=?,finished_at=?,lease_until=0
                WHERE id=? AND owner=? AND state='working'"""),
                ("error" if error else "done",json.dumps(result),time.time(),task["id"],self.owner))

    def cached_ids(self, signature):
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute(db._q("SELECT channel_id FROM niche_discovery_cache WHERE signature=? AND expires_at>?"),
                        (signature,time.time()))
            return {dict(r)["channel_id"] for r in cur.fetchall()}

    def remember(self, channel_id, signature, result, ttl):
        now = time.time()
        with db._conn() as conn:
            conn.cursor().execute(db._q("""INSERT INTO niche_discovery_cache
                (channel_id,signature,expires_at,result_json,checked_at) VALUES (?,?,?,?,?)
                ON CONFLICT(channel_id) DO UPDATE SET signature=excluded.signature,
                    expires_at=excluded.expires_at,result_json=excluded.result_json,checked_at=excluded.checked_at"""),
                (channel_id,signature,now+ttl,json.dumps(result),now))

    def existing_ids(self):
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT channel_id FROM niche_channels")
            return {dict(r)["channel_id"] for r in cur.fetchall()}

    def unreviewed_ids(self):
        with db._conn() as conn:
            cur=conn.cursor()
            cur.execute("SELECT channel_id FROM niche_channels WHERE active=1 AND COALESCE(quality_status,'')!='screened'")
            return {dict(r)["channel_id"] for r in cur.fetchall()}

    def seed_channels(self, limit=40):
        # Draw across the existing library rather than a hand-picked topic list.
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT channel_id,channel_name,recent_videos_json,popular_videos_json,source_keyword
                FROM niche_channels WHERE active=1 AND recent_avg_views>=10000
                AND video_count<=150 AND subscriber_count BETWEEN 1000 AND 300000""")
            rows = [dict(r) for r in cur.fetchall()]
        rows.sort(key=lambda r: hashlib.sha256((self.job_id+r["channel_id"]).encode()).hexdigest())
        return rows[:limit]

    def enrichment_candidates(self, limit=80):
        """Choose promising factories across source groups without a topic allowlist."""
        with db._conn() as conn:
            cur=conn.cursor()
            cur.execute("""SELECT channel_id,source_keyword FROM niche_channels
                WHERE active=1 AND COALESCE(quality_status,'')!='screened'
                AND recent_avg_views>=20000 AND videos_last_14d>0
                AND video_count BETWEEN 4 AND 80 AND subscriber_count BETWEEN 1000 AND 500000""")
            rows=[dict(r) for r in cur.fetchall()]
        groups={}
        for row in rows: groups.setdefault(row.get('source_keyword') or 'unlabelled',[]).append(row)
        for group in groups.values():
            group.sort(key=lambda r:hashlib.sha256((self.job_id+r['channel_id']).encode()).hexdigest())
        sources=sorted(groups,key=lambda s:hashlib.sha256((self.job_id+s).encode()).hexdigest())
        chosen=[]
        while sources and len(chosen)<limit:
            for source in list(sources):
                if len(chosen)>=limit: break
                chosen.append(groups[source].pop())
                if not groups[source]: sources.remove(source)
        return chosen

    def publish(self, hit, review, performance, task, outcome):
        if review.get("decision") != "pass" or review.get("ai_reproducible") is not True or not performance.get("passes"):
            raise ValueError("Only screened performance-qualified channels can be published")
        return self._publish(hit,review,performance,task,outcome)

    def publish_lead(self, hit, performance, task, outcome):
        if not performance.get("passes"):
            raise ValueError("Only performance-qualified leads can be published")
        return self._publish(hit,None,performance,task,outcome)

    def _publish(self, hit, review, performance, task, outcome):
        # Admission and its checkpoint commit together. Cancellation locks the same
        # run row, so a cancelled worker cannot continue inserting channels.
        with db._conn() as conn:
            cur=conn.cursor()
            if not db.IS_PG: cur.execute("BEGIN IMMEDIATE")
            lock=" FOR UPDATE" if db.IS_PG else ""
            cur.execute(db._q("SELECT status FROM niche_hunt_runs WHERE job_id=?"+lock),(self.job_id,))
            run=cur.fetchone()
            cur.execute(db._q("SELECT owner FROM niche_discovery_lease WHERE job_id=? AND owner=? AND expires_at>?"),
                        (self.job_id,self.owner,time.time()))
            lease=cur.fetchone()
            if not run or dict(run)["status"]!="running" or not lease:
                return "cancelled"
            cur.execute(db._q("SELECT id FROM niche_discovery_tasks WHERE id=? AND owner=? AND state='working'"),
                        (task["id"],self.owner))
            if not cur.fetchone(): return "cancelled"
            added=db.upsert_niche_channel(hit,connection=conn,insert_only=True)
            if not added and (review is None or not task.get("payload",{}).get("review_existing")):
                return "already_present"
            if not added:
                cur.execute(db._q("SELECT active FROM niche_channels WHERE channel_id=?"+lock),(hit["channel_id"],))
                row=cur.fetchone()
                if not row or dict(row)["active"]!=1: return "already_present"
                db.upsert_niche_channel(hit,connection=conn)
            cur.execute(db._q("""UPDATE niche_channels SET production_format=?,avatar_confidence=?,
                quality_status=?,quality_evidence_json=?,discovery_job_id=? WHERE channel_id=?"""),
                ((review or {}).get("production_format", "unknown"),(review or {}).get("avatar_confidence", "unknown"),
                 "screened" if review else "metrics",
                 json.dumps({"performance":performance,"content":review,"checked_at":time.time()}),self.job_id,hit["channel_id"]))
            status="added" if added else "enriched_existing"
            saved={**outcome,"status":status}
            cur.execute(db._q("""UPDATE niche_discovery_tasks SET state='done',result_json=?,finished_at=?,lease_until=0
                WHERE id=? AND owner=? AND state='working'"""),
                (json.dumps(saved),time.time(),task["id"],self.owner))
            if added:
                cur.execute(db._q("UPDATE niche_hunt_runs SET channels_upserted=channels_upserted+1 WHERE job_id=?"),(self.job_id,))
            return status

    def completed_results(self):
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute(db._q("SELECT result_json FROM niche_discovery_tasks WHERE job_id=? AND kind='channel' AND state='done'"), (self.job_id,))
            return [json.loads(dict(r)["result_json"]) for r in cur.fetchall()]

    def reconsider_review_holds(self):
        """Retry only uncertain reviews after a classifier change; never replay additions."""
        with db._conn() as conn:
            cur=conn.cursor()
            cur.execute(db._q("SELECT id,result_json FROM niche_discovery_tasks WHERE job_id=? AND kind='channel' AND state='done' AND attempts<3"),(self.job_id,))
            for row in cur.fetchall():
                row=dict(row);result=json.loads(row['result_json'])
                if result.get('status') in {'review','avatar_hold'} and result.get('performance',{}).get('passes'):
                    cur.execute(db._q("UPDATE niche_discovery_tasks SET state='queued',owner='',lease_until=0 WHERE id=? AND state='done'"),(row['id'],))

    def task_counts(self):
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute(db._q("SELECT kind,state,count(*) AS n FROM niche_discovery_tasks WHERE job_id=? GROUP BY kind,state"),(self.job_id,))
            return [dict(r) for r in cur.fetchall()]
