"""Correlate a current native handoff with a boot-scoped host signature. Observe only."""
import hashlib
import hmac
import json
from secrets import token_hex

from .errors import ServiceError
from .runtime_proof import signature


def payload(row):
    return {k: str(row[k]) if k in ('session_id','boot_id') else row[k]
            for k in ('cluster_id','gpu_uuid','worker_id','session_id','boot_id','nonce')}


def challenge(db, cluster, gpu, boot):
    with db.transaction() as c:
        row = c.execute('''SELECT * FROM boot_observation_source WHERE cluster_id=%s AND gpu_uuid=%s
            AND boot_id=%s AND eligible AND proof IS NULL AND due>clock_timestamp()''',
                        (cluster,gpu,boot)).fetchone()
        return payload(row) if row else None


def receive(db, cluster, report, key):
    with db.transaction() as c:
        row = c.execute('''SELECT * FROM boot_observation_source WHERE cluster_id=%s AND gpu_uuid=%s
            AND boot_id=%s AND nonce=%s AND eligible FOR UPDATE''',
                        (cluster,report.gpu_uuid,report.boot_id,report.nonce)).fetchone()
        if not row or not hmac.compare_digest(signature(key,'boot-observe',payload(row)),report.signature):
            raise ServiceError('BOOT_PROOF_REJECTED','Startup challenge does not match',409)
        if row['proof'] == report.signature:
            return
        if not c.execute('''UPDATE boot_observation_source SET proof=%s WHERE cluster_id=%s AND gpu_uuid=%s
                AND due>clock_timestamp() RETURNING seq''', (report.signature,cluster,report.gpu_uuid)).fetchone():
            raise ServiceError('BOOT_PROOF_EXPIRED','Startup challenge expired',409)


def grant_session(conn, cluster, gpu, boot):
    if cluster is None or boot is None:
        return None
    row = conn.execute('''SELECT h.session_id FROM boot_observation_source s JOIN boot_session_history h
        USING(cluster_id,gpu_uuid,seq,session_id,boot_id) WHERE s.cluster_id=%s AND s.gpu_uuid=%s
        AND s.boot_id=%s AND s.eligible''',(cluster,gpu,boot)).fetchone()
    return row['session_id'] if row else None


def observe_source(c, cluster, source):
    gpu, seq, worker, session, boot = (source[k] for k in ('gpu_uuid','seq','worker_id','session_id','boot_id'))
    row = c.execute('''SELECT * FROM boot_observation_source WHERE cluster_id=%s AND gpu_uuid=%s FOR UPDATE''',
                    (cluster,gpu)).fetchone()
    if row and seq < row['seq']:
        return
    if row and seq == row['seq'] and (str(row['session_id']) != session or row['worker_id'] != worker
                                      or (row['boot_id'] and boot and str(row['boot_id']) != boot)):
        c.execute('''UPDATE boot_observation_source SET eligible=false,nonce=NULL,due=NULL,proof=NULL
            WHERE cluster_id=%s AND gpu_uuid=%s''',(cluster,gpu))
        return
    previous = c.execute('''SELECT * FROM boot_session_history WHERE cluster_id=%s AND gpu_uuid=%s
        ORDER BY seq DESC LIMIT 1''',(cluster,gpu)).fetchone()
    same_boot = bool(previous and str(previous['boot_id']) == boot)
    retired = boot and not same_boot and c.execute('''SELECT 1 FROM boot_session_history
        WHERE cluster_id=%s AND gpu_uuid=%s AND boot_id=%s''',(cluster,gpu,boot)).fetchone()
    eligible = bool(boot and not retired)
    if not row or seq != row['seq'] or eligible != row['eligible'] or (boot and not row['boot_id']):
        c.execute('''INSERT INTO boot_observation_source(cluster_id,gpu_uuid,seq,worker_id,session_id,boot_id,eligible)
            VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(cluster_id,gpu_uuid) DO UPDATE SET
            seq=excluded.seq,worker_id=excluded.worker_id,session_id=excluded.session_id,boot_id=excluded.boot_id,
            eligible=excluded.eligible,nonce=NULL,due=NULL,proof=NULL''',(cluster,gpu,seq,worker,session,boot,eligible))
        row = None
    if not eligible or (previous and seq <= previous['seq']):
        return
    if row and row['proof']:
        # Serialize the transition and its candidate scan with grants and
        # UNKNOWN/finish writes. Never hold this lock while querying GPUStack.
        c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
        generation = previous['generation'] + int(not same_boot) if previous else 1
        c.execute('''INSERT INTO boot_session_history(cluster_id,gpu_uuid,seq,worker_id,session_id,boot_id,
            generation,nonce,proof) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                  (cluster,gpu,seq,worker,session,boot,generation,row['nonce'],row['proof']))
        return
    c.execute("""UPDATE boot_observation_source SET nonce=%s,due=clock_timestamp()+interval '2 minutes'
        WHERE cluster_id=%s AND gpu_uuid=%s AND (due IS NULL OR due<=clock_timestamp())""",(token_hex(32),cluster,gpu))


def observe(db, cluster, sources):
    """sources supplies a fresh full native snapshot *after* taking the observer lock."""
    with db.transaction() as c:
        c.execute('SELECT pg_advisory_xact_lock(842125229)')
        c.execute("SET LOCAL idle_in_transaction_session_timeout = '12s'")
        # Verify native active *after* the signed response. A concurrent host
        # report waits for the next sample and cannot use an earlier native read.
        c.execute('SELECT seq FROM boot_observation_source WHERE cluster_id=%s ORDER BY gpu_uuid FOR UPDATE',
                  (cluster,)).fetchall()
        rows = sources()
        gpus = [s['gpu_uuid'] for s in rows]
        if len(set(gpus)) != len(gpus):
            raise ValueError('Ambiguous GPU ownership')
        c.execute('''UPDATE boot_observation_source SET eligible=false,proof=NULL,nonce=NULL,due=NULL
            WHERE cluster_id=%s AND eligible AND NOT (gpu_uuid=ANY(%s))''',(cluster,gpus))
        for row in rows:
            observe_source(c,cluster,row)
        c.execute('''INSERT INTO boot_unknown_candidates(kind,token,cluster_id,gpu_uuid,old_seq,new_seq,unknown_since)
            SELECT * FROM current_boot_unknown_candidates WHERE cluster_id=%s ON CONFLICT DO NOTHING''',(cluster,))
        result = dict(c.execute('''SELECT (SELECT count(*) FROM boot_session_history WHERE cluster_id=%s) confirmations,
            (SELECT count(*) FROM boot_unknown_candidates WHERE cluster_id=%s) recorded_candidates,
            (SELECT count(*) FROM current_boot_unknown_candidates WHERE cluster_id=%s) pending_candidates''',
                              (cluster,cluster,cluster)).fetchone())
        result.update(native_active=len(rows), enrolled_active=sum(bool(s['boot_id']) for s in rows))
        return result


if __name__ == '__main__':
    import sys
    from .config import Settings
    from .db import Database
    s = Settings.from_env()
    if len(sys.argv) != 2 or int(sys.argv[1]) != s.boot_observe_cluster:
        raise SystemExit('Boot observation is disabled')
    def snapshot():
        # The privileged collector reads GPUStack after this handshake. A dying pipe rolls back.
        print('snapshot',flush=True)
        from .runtime_recovery import host_key
        rows = json.loads(sys.stdin.readline())
        for row in rows:
            boot = row['boot_id']
            if boot:
                key = host_key(s,row['gpu_uuid'],boot)
                if not key or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(),row['commitment']):
                    row['boot_id'] = None
        return rows
    print(json.dumps(observe(Database(s.database_url), s.boot_observe_cluster, snapshot)))
