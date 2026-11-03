"""Compose the E2 draft with the customer API contract."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def merge_batch(doc):
    draft = yaml.safe_load((ROOT / 'docs/api/batch.openapi.yaml').read_text())
    doc['info']['description'] += ' E2 批次接口及文件接口的 JSONL/ZIP 扩展为待实现草案。'

    schemas = draft['components']['schemas']
    batch_error = schemas.pop('ApiError')
    schemas.pop('ArtifactCreated')
    schemas['BatchApiError'] = batch_error
    target = doc['components']['schemas']
    assert not target.keys() & schemas.keys(), 'E2 schema name collision'
    target.update(schemas)

    paths = draft['paths']
    upload = paths.pop('/maas/v1/artifacts')['post']
    download = paths.pop('/maas/v1/artifacts/{artifact_id}')['get']
    existing_upload = doc['paths']['/maas/v1/artifacts']['post']
    existing_download = doc['paths']['/maas/v1/artifacts/{ident}']['get']
    additions = (
        (existing_upload, existing_upload['requestBody'], upload['requestBody'], ('application/x-ndjson',)),
        (existing_download, existing_download['responses']['200'], download['responses']['200'],
         ('application/x-ndjson', 'application/zip')),
    )
    for operation, target, source, media_types in additions:
        operation['description'] += ' E2 批次媒体类型扩展待实现。'
        for media_type in media_types:
            media = source['content'][media_type]
            media['x-implementation-status'] = 'draft'
            target['content'][media_type] = media

    for path, item in paths.items():
        assert path not in doc['paths'], path
        for method, operation in item.items():
            if method == 'parameters':
                continue
            operation['x-implementation-status'] = 'draft'
            operation['description'] = 'E2 草案，待实现。' + operation.get('description', '')
            for response in operation['responses'].values():
                schema = response.get('content', {}).get('application/json', {}).get('schema')
                if schema == {'$ref': '#/components/schemas/ApiError'}:
                    schema['$ref'] = '#/components/schemas/BatchApiError'
        doc['paths'][path] = item


if __name__ == '__main__':
    path = ROOT / 'docs/api/openapi.json'
    doc = json.loads(path.read_text())
    merge_batch(doc)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
