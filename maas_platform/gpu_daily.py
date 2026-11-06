"""Build and serve immutable E17 daily reports."""
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from .gpu_time import BEIJING


MAX_GAP = 120


def bounds(day):
    return tuple(int(datetime.combine(d, time.min, BEIJING).timestamp())
                 for d in (day, day + timedelta(days=1)))


def build(day, observations):
    start, end = bounds(day)
    intervals = {}
    covered = 0
    previous = None
    for at, session, cards in observations:
        if previous is not None:
            before, prior_session, prior_cards = previous
            left, right = max(start, before), min(end, at)
            if session == prior_session and 0 < at - before <= MAX_GAP and left < right:
                covered += right - left
                for uuid, models in prior_cards.items():
                    segments = intervals.setdefault(uuid, [])
                    if not segments or segments[-1][1] != left:
                        segments.append([left, right, {}])
                    segment = segments[-1]
                    segment[1] = right
                    for model in models:
                        key = (model['gpustack_deployment_id'], model['logical_model_name'] or '')
                        segment[2][key] = model
        previous = at, session, cards

    def stamp(seconds):
        return datetime.fromtimestamp(seconds, BEIJING).isoformat()

    return {'report_id': f'gpu-daily-{day}', 'date': str(day), 'timezone': str(BEIJING),
            'coverage': {'covered_seconds': covered, 'total_seconds': end - start,
                         'percent': round(100 * covered / (end - start), 2)},
            'records': [
                {'gpu_uuid': uuid, 'online_from': stamp(left), 'online_until': stamp(right),
                 'models': [models[key] for key in sorted(models)]}
                for uuid, segments in sorted(intervals.items()) for left, right, models in segments
            ]}


def seal(db, day):
    start, end = bounds(day)
    with db.transaction() as conn:
        rows = conn.execute(
            'SELECT at,session_id,cards FROM e17.daily_observations WHERE at BETWEEN %s AND %s ORDER BY at',
            (start - MAX_GAP, end + MAX_GAP),
        )
        payload = build(day, ((r['at'], r['session_id'], r['cards']) for r in rows))
        # No confirmed interval means the report is unavailable.
        if payload['coverage']['covered_seconds']:
            conn.execute('INSERT INTO e17.daily_reports(day,payload) VALUES (%s,%s) ON CONFLICT (day) DO NOTHING',
                         (day, Jsonb(payload)))


def router(db):
    routes = APIRouter(prefix='/maas/v1/gpu-daily-reports', include_in_schema=False)

    @routes.get('/{day}')
    def get(day: date):
        with db.transaction() as conn:
            row = conn.execute('SELECT payload FROM e17.daily_reports WHERE day=%s', (day,)).fetchone()
        if not row:
            raise HTTPException(404, {'code': 'GPU_DAILY_REPORT_UNAVAILABLE'})
        return JSONResponse(row['payload'], headers={'Cache-Control': 'no-store'})

    return routes
