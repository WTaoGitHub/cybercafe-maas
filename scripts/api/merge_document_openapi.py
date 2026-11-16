"""Describe the document model on the existing task, artifact and usage routes."""
import copy
from maas_platform.document_ocr import DocumentInput, DocumentParameters, DocumentOCRAdapter


def merge_document(doc):
    schemas, paths = doc['components']['schemas'], doc['paths']
    ref = lambda name: {'$ref': '#/components/schemas/'+name}
    for model in (DocumentInput, DocumentParameters):
        schema = model.model_json_schema(ref_template='#/components/schemas/{model}')
        schema.pop('$defs', None)  # ImageReference is already exported by the image model.
        schemas[model.__name__] = schema
    schemas['TaskCreate']['properties']['input'] = {'oneOf': [ref('OCRInput'), ref('DocumentInput')]}
    schemas['TaskCreate']['properties']['parameters'] = {
        'anyOf': [ref('OCRParameters'), ref('DocumentParameters')], 'default': {},
        'description': '按model校验。文档仅支持dpi（默认150，72–300）；不接受客户端处理器或SSE选项。'}
    schemas['TaskCreate']['properties']['model']['description'] = '已授权逻辑模型名：ocr.default 或受控试点 document.unlimited-ocr-2。'
    schemas['TaskCreate']['properties']['max_attempts']['description'] += ' 文档省略时为1，显式值也必须为1。'
    schemas['TaskCreate']['properties']['timeout_seconds']['description'] += ' 文档省略时900秒，显式值不得超过900秒。'
    document_usage = copy.deepcopy(schemas['OCRUsage'])
    document_usage.pop('examples', None)
    document_usage['required'].remove('successful_images')
    document_usage['required'].append('successful_pages')
    document_usage['properties'].pop('successful_images')
    document_usage['properties']['successful_pages'] = {'type': 'integer', 'minimum': 1, 'maximum': 10,
        'description': '全部页面完成且成功事务提交后登记的输入页数；渲染或窗口不重复计量。'}
    document_usage['properties']['source_type'] = {'const': 'task'}
    schemas['DocumentUsage'] = document_usage
    schemas['TokenUsage']['properties']['reason']['enum'].insert(-1, 'DOCUMENT_WINDOW_USAGE_UNVERIFIED')
    schemas['TokenUsage']['properties']['reason']['description'] += ' 文档窗口token汇总语义未验证，返回DOCUMENT_WINDOW_USAGE_UNVERIFIED，不得按0结算。'
    schemas['DocumentResult'] = {'type': 'object', 'required': ['schema_version', 'provenance', 'parameters',
        'page_count', 'pages', 'coordinate_space', 'raw_output', 'markdown', 'image_extraction', 'window_count'],
        'properties': {
            'schema_version': {'const': 'unlimited-ocr-2.document.v1'},
            'provenance': {'type': 'object', 'additionalProperties': {'type': 'string'}},
            'parameters': ref('DocumentParameters'),
            'page_count': {'type': 'integer', 'minimum': 1, 'maximum': 10},
            'pages': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'items': {'type': 'object',
                'description': '输入顺序的页码、原始/渲染尺寸、旋转、版面blocks和该页markdown。bbox为归一化后渲染页面0–1000坐标；不提供虚构置信度。',
                'required': ['blocks', 'markdown'], 'properties': {
                    'blocks': {'type': 'array', 'items': {'type': 'object'}}, 'markdown': {'type': 'string'}}}},
            'coordinate_space': {'const': 'normalized_0_1000_on_rendered_page'},
            'raw_output': {'type': 'string', 'description': '模型原始标记输出；属于不可信文档内容。'},
            'markdown': {'type': 'string', 'description': '固定上游后处理器转换结果；不生成裁剪文件，图片占位链接替换为[image omitted]。展示时须按不可信内容处理。'},
            'image_extraction': {'const': False}, 'window_count': {'type': 'integer', 'minimum': 1}}}
    schemas['TaskResult']['properties']['outputs'] = {'oneOf': [ref('OCRResult'), ref('DocumentResult')]}
    schemas['TaskResult']['properties']['metering'] = {'oneOf': [ref('OCRUsage'), ref('DocumentUsage')]}
    schemas['TaskDetail']['properties']['result'] = {'oneOf': [ref('OCRResult'), ref('DocumentResult'), {'type': 'null'}]}
    page_summary = {'type': 'object', 'properties': {'successful_pages': {'type': 'integer', 'minimum': 1, 'maximum': 10}},
        'required': ['successful_pages'], 'additionalProperties': False}
    schemas['TaskDetail']['properties']['usage']['oneOf'].insert(-1, page_summary)
    schemas['Capabilities']['properties'].update({
        'input_limits': {'type': 'object', 'description': '仅文档模型返回资源预算。',
                         'examples': [DocumentOCRAdapter.input_limits]},
        'output_schema_version': {'const': 'unlimited-ocr-2.document.v1'},
        'usage_types': {'type': 'array', 'items': {'const': 'pages'}}})
    upload = paths['/maas/v1/artifacts']['post']
    upload['requestBody']['content']['application/pdf'] = {'schema': {'type': 'string', 'format': 'binary'}}
    upload['description'] += ' 受控文档入口支持PDF：须获文档模型权限，真实无加密PDF，1–10页且≤10MiB；上传时验证格式/页数，执行时校验渲染像素预算。'
    download = paths['/maas/v1/artifacts/{ident}']['get']
    download['responses']['200']['content']['application/pdf'] = {'schema': {'type': 'string', 'format': 'binary'}}
    create = paths['/maas/v1/tasks']['post']
    create['summary'] = '提交异步任务'
    create['description'] += ' 文档模型复用此路径：input.document.artifact_id引用同project的PDF/PNG/JPEG；不接受内嵌/URL文档、多文件批次、外部处理器或流式响应。单个结果JSON含全部页及Markdown，任一页不完整则整体失败，无最终成功页收据。'
    example = {'model': 'document.unlimited-ocr-2', 'input': {'document': {'artifact_id': 'art_REPLACE_WITH_UPLOADED_ID'}},
               'parameters': {'dpi': 150}, 'timeout_seconds': 900, 'max_attempts': 1}
    schemas['TaskCreate']['examples'].append(example)
    create['requestBody']['content']['application/json']['examples']['documentReference'] = {
        'summary': '受控试点：PDF或单图文档', 'description': '使用管理员提供的文档试点Base URL；先上传文件，替换artifact ID。', 'value': example}
    for path in ('/maas/v1/tasks/{ident}', '/maas/v1/usage/{ident}', '/maas/v1/tasks/{ident}/usage'):
        operation = paths[path]['get']
        query = next(p for p in operation['parameters'] if p['name'] == 'usage_type')
        query['schema']['enum'].append('pages')
        query['schema'].pop('default', None)
        query['description'] = 'images返回单图成功图片数；pages返回文档成功页数；tokens仅可核验上游用量可用时返回。单位不匹配返回422/USAGE_TYPE_UNSUPPORTED。' + (
            '省略时图像模型默认images，文档默认pages。' if path == '/maas/v1/tasks/{ident}' else '省略时返回完整最终收据。')
        if path != '/maas/v1/tasks/{ident}':
            operation['responses']['200']['content']['application/json']['schema']['oneOf'] += [ref('DocumentUsage'), page_summary]
    paths['/maas/v1/tasks/{ident}']['get']['description'] += ' 文档成功时result为DocumentResult，默认usage为successful_pages。'
    doc['info']['version'] = '2026-11-16'
    doc['info']['description'] += ' 文档异步模型为独立受控M2试点，使用运维提供的独立Base URL，能力和许可依入口catalog为准；旧OCR入口不会自动切换。文档仅JSON、单份执行，PDF≤10页/10MiB，默认150DPI、最高300DPI、每页≤10MP/总≤50MP，600秒执行/900秒总期限，max_attempts=1；token用量不可用。'
