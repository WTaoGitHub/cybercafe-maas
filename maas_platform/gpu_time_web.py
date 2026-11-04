"""Operator-only GPU uptime reports served by the MaaS API."""
from base64 import b64encode
from datetime import datetime
from hashlib import sha256
from hmac import compare_digest
from html import escape
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .gpu_time import BEIJING, MAX_GAP_SECONDS, report


STYLE = """
body{margin:0;background:#eef3f8;color:#19283a;font:16px system-ui,sans-serif}
main{max-width:1050px;margin:auto;padding:2rem 1rem}
h1{margin:0;color:#12345b} h2{margin-top:0;font-size:1.15rem}
section,form{background:white;border:1px solid #d6e2ef;border-radius:12px;padding:1.15rem;margin:1rem 0}
form{display:flex;gap:1rem;align-items:end;flex-wrap:wrap}
label{display:grid;gap:.35rem} input,button{font:inherit;padding:.5rem;border:1px solid #8297ad;border-radius:6px}
button{background:#145694;color:white;cursor:pointer}
.table{overflow:auto} table{border-collapse:collapse;width:100%;white-space:nowrap}
th,td{text-align:left;padding:.65rem;border-bottom:1px solid #d6e2ef}
thead{background:#e7eff8} .number{font-size:1.8rem;color:#145694} p{line-height:1.5}
"""
STYLE_HASH = b64encode(sha256(STYLE.encode()).digest()).decode()


def timestamp(value):
    return datetime.fromtimestamp(value, BEIJING).strftime('%Y-%m-%d %H:%M:%S')


def router(db):
    routes = APIRouter(prefix='/maas/ops/gpu-time', include_in_schema=False)
    auth = HTTPBasic(auto_error=False)
    key_path = os.environ.get('E17_QUERY_KEY_FILE')
    key = Path(key_path).read_bytes().strip() if key_path else b''
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
               'Content-Security-Policy': f"default-src 'none'; style-src 'sha256-{STYLE_HASH}'; form-action 'self'; frame-ancestors 'none'"}

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
        coverage = data['coverage']
        percent = '—' if coverage['percent'] is None else f"{coverage['percent']:.2f}%"
        segments = ''.join(
            f'<tr><td>{timestamp(part["start_at"])}</td><td>{timestamp(part["end_at"])}</td>'
            f'<td>{part["seconds"]:,}</td></tr>' for part in coverage['intervals']
        ) or '<tr><td colspan="3">没有已确认的连续采样区间</td></tr>'
        return HTMLResponse(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>GPU 在线时长</title><style>{STYLE}</style>'
            '<main><h1>GPU 在线时长</h1><p>北京时间 · 时长单位为秒</p>'
            f'<p>最近成功采样 {freshness}</p><form method="get">'
            f'<label>日期或月份 <input name="period" value="{escape(data["period"])}" '
            'placeholder="YYYY-MM-DD 或 YYYY-MM" required></label>'
            f'<label>GPU UUID <input name="gpu_uuid" value="{escape(gpu_uuid)}"></label>'
            '<button>查询</button></form>'
            '<section><h2>总体采样覆盖率</h2>'
            f'<strong class="number">{percent}</strong>'
            f'<p>有效 {coverage["effective_seconds"]:,} 秒 / 查询窗口 {data["period_seconds"]:,} 秒'
            f' · 未覆盖 {coverage["unknown_seconds"]:,} 秒</p>'
            '<p>同一采样会话中，相邻两轮成功且间隔不超过 120 秒才计入覆盖。'
            '失败、停采和最后一轮之后尚未确认的时间均不计入。</p>'
            '<details><summary>连续有效采样区间</summary><div class="table"><table>'
            '<thead><tr><th>开始</th><th>结束</th><th>有效秒数</th></tr></thead>'
            f'<tbody>{segments}</tbody></table></div></details></section>'
            '<section><h2>逐卡时长</h2><div class="table"><table><thead><tr>'
            '<th>GPU UUID</th><th>在线</th><th>非 ready</th><th>未知</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>'
            '<p>在线和非 ready 来自可明确归属的 worker 状态。'
            '未知包括未覆盖时段，以及采样成功但该卡未上报或记录冲突的时段。'
            '因此单卡覆盖率可以低于总体覆盖率。</p></section></main></html>',
            headers=headers,
        )

    return routes
