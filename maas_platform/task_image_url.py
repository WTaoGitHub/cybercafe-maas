"""Bounded pre-admission image fetch; no network waits under global/file locks."""
import asyncio
import hashlib
from contextlib import contextmanager
import psycopg
from .errors import ServiceError
from .image_fetch import PublicImageFetcher, image_url
from .consumer_ocr import validate_image

MAX_GLOBAL_DOWNLOADS = 4
MAX_PROJECT_DOWNLOADS = 2
DOWNLOAD_SECONDS = 10


def lock_key(*parts):
    # Namespaced and length-delimited; never persist or log raw/signed URLs.
    value=''.join(str(len(p))+':'+p for p in ('maas-task-image-url-v1',*parts))
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'big', signed=True)


@contextmanager
def download_slot(database_url, project, idem):
    # Dedicated session: automatic release on exit/disconnect, no transaction
    # spanning the network wait, no new state table or long-lived lock owner.
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5,
                         options='-c statement_timeout=2000 -c lock_timeout=1000') as conn:
        def take(key):return conn.execute('SELECT pg_try_advisory_lock(%s)',(key,)).fetchone()[0]
        if not take(lock_key('idem',project,idem)):
            raise ServiceError('IDEMPOTENCY_IN_PROGRESS','Same-key image preparation is in progress; retry the same request later',409,True)
        for scope,count in ((('project',project),MAX_PROJECT_DOWNLOADS),(('global',),MAX_GLOBAL_DOWNLOADS)):
            if not any(take(lock_key(*scope,str(i))) for i in range(count)):
                raise ServiceError('IMAGE_FETCH_CAPACITY','Image download capacity reached',429,True)
        yield


async def fetch_image(url, limit):
    try:
        async with asyncio.timeout(DOWNLOAD_SECONDS):
            return await PublicImageFetcher().fetch(url,limit)
    except TimeoutError:
        raise ServiceError('IMAGE_FETCH_TIMEOUT','Image preparation exceeded download budget',408) from None


def submit_url(service, identity, body, idem, model, inp, params):
    from .service import fingerprint
    url=inp['image']['url']; image_url(url)
    digest=fingerprint(body.model_dump())
    def preflight():
        with service.db.transaction() as conn:
            old=conn.execute('SELECT * FROM tasks WHERE project=%s AND idempotency_key=%s',(identity.project,idem)).fetchone()
            if old:
                if old['request_hash']!=digest:
                    raise ServiceError('IDEMPOTENCY_CONFLICT','Key already used for different content',409)
                return service.public(old),False
            counts=conn.execute("SELECT count(*) total,count(*) FILTER(WHERE project=%s) own FROM tasks WHERE state IN ('QUEUED','RETRY_WAIT')",(identity.project,)).fetchone()
            if counts['total']>=service.settings.max_queued or counts['own']>=service.settings.max_project_queued:
                raise ServiceError('QUEUE_FULL','Task queue capacity reached',429,True)
            # An early lower-bound check only; final byte-accurate check remains atomic.
            service.check_storage(conn,identity.project,0)
        service.store.ensure_space()
    old=preflight()
    if old:return old
    with download_slot(service.db.url,identity.project,idem):
        old=preflight()
        if old:return old
        raw=asyncio.run(fetch_image(url,service.settings.max_input_bytes))
        if not raw or len(raw)>service.settings.max_input_bytes:
            raise ServiceError('INPUT_SIZE','Empty or oversized image',413)
        media=validate_image(raw)
        fresh=service.auth.for_task(identity.credential_id,identity.project)
        current=service.model(body.model);service.auth.authorize(fresh,body.model,current)
        if fingerprint(current)!=fingerprint(model):
            raise ServiceError('MODEL_REVISION_CHANGED','Model changed during input preparation',409)
        return service._accept(fresh,body,idem,model,inp,params,None,(raw,media))
