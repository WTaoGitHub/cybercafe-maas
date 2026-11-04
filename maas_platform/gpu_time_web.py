"""Operator-only GPU uptime reports served by the MaaS API."""
from datetime import datetime
from hmac import compare_digest
from html import escape
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .gpu_time import BEIJING, MAX_GAP_SECONDS, report


def router(db):
    routes = APIRouter(prefix='/maas/ops/gpu-time', include_in_schema=False)
    auth = HTTPBasic(auto_error=False)
    key_path = os.environ.get('E17_QUERY_KEY_FILE')
    key = Path(key_path).read_bytes().strip() if key_path else b''
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
               'Content-Security-Policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'"}

    def operator(credentials: HTTPBasicCredentials | None = Depends(auth)):
        if not key:
            raise HTTPException(404)
        if (credentials is None or credentials.username != 'e17'
                or not compare_digest(credentials.password.encode(), key)):
            raise HTTPException(401, headers={'WWW-Authenticate': 'Basic realm="MaaS E17"'})

    def query(period: str | None = Query(None, max_length=10),
              gpu_uuid: str | None = Query(None, max_length=128)):
        now = datetime.now(BEIJING)
        try:
            data = report(db, period if period is not None else now.date().isoformat(), int(now.timestamp()))
        except (ValueError, OverflowError):
            raise HTTPException(422, 'Use YYYY-MM-DD or YYYY-MM') from None
        if gpu_uuid:
            data['gpus'] = {uuid: row for uuid, row in data['gpus'].items() if uuid == gpu_uuid}
        return data

    @routes.get('/report', dependencies=[Depends(operator)])
    def report_json(data=Depends(query)):
        return JSONResponse(data, headers=headers)

    @routes.get('', dependencies=[Depends(operator)], response_class=HTMLResponse)
    def page(data=Depends(query), gpu_uuid: str = ''):
        rows = ''.join(
            f'<tr><th scope="row">{escape(uuid)}</th><td>{row["online_seconds"]}</td>'
            f'<td>{row["not_ready_seconds"]}</td><td>{row["unknown_seconds"]}</td></tr>'
            for uuid, row in data['gpus'].items()
        ) or '<tr><td colspan="4">没有匹配的 GPU 数据</td></tr>'
        last = data['last_success_at']
        freshness = '暂无成功采样' if last is None else datetime.fromtimestamp(last, BEIJING).isoformat()
        if data['sampling_stale'] and last is not None:
            freshness += '（采样已过期）'
        return HTMLResponse(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>GPU 在线时长</title>'
            '<main><h1>GPU 在线时长</h1><p>北京时间 · 时长单位为秒</p>'
            f'<p>最近成功采样 {freshness}</p><form method="get">'
            f'<label>日期或月份 <input name="period" value="{escape(data["period"])}" '
            'placeholder="YYYY-MM-DD 或 YYYY-MM" required></label> '
            f'<label>GPU UUID <input name="gpu_uuid" value="{escape(gpu_uuid)}"></label> '
            '<button>查询</button></form><table><thead><tr><th>GPU UUID</th><th>在线</th>'
            f'<th>非 ready</th><th>未知</th></tr></thead><tbody>{rows}</tbody></table></main></html>',
            headers=headers,
        )

    return routes
