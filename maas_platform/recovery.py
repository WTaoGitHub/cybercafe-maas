"""Private real-inference recovery probes; never exposed as a customer route."""
import asyncio,base64,json
from pathlib import Path
from .errors import ServiceError
from .ocr_connector import OCRResult
from .sync_ingress import SyncIngress


def probe_registered_models(service,credential_id,image_path):
    models={name:model for name,model in service.settings.catalog().items() if model.get('upstream_model')}
    if not models:return []
    if not credential_id or not image_path:
        raise ValueError('Recovery requires --credential-id and --probe-image for network models')
    binding=next((r for r in service.auth.bindings() if r['id']==credential_id),None)
    if not binding:raise ValueError('Recovery credential is not registered')
    who=service.auth.for_task(binding['id'],binding['project'])
    with Path(image_path).open('rb') as f:data=f.read(service.settings.max_input_bytes+1)
    media='image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else ('image/jpeg' if data.startswith(b'\xff\xd8\xff') else None)
    if not media or len(data)>service.settings.max_input_bytes:raise ValueError('Probe must be a bounded PNG/JPEG file')
    outputs=[];ingress=SyncIngress(service)
    for name,model in models.items():
        if model['driver']!='gpustack.ocr':
            raise ServiceError('RECOVERY_UNSUPPORTED','Register a recovery probe for each network connector',409)
        body={'model':name,'stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:'+media+';base64,'+base64.b64encode(data).decode()}}]}]}
        response=asyncio.run(ingress.infer(who,body,probe=True))
        try:
            result=OCRResult.model_validate(json.loads(json.loads(response.body)['choices'][0]['message']['content']))
        except (ValueError,KeyError,IndexError,TypeError):
            raise ServiceError('RECOVERY_OUTPUT','Recovery response is not valid OCR output',502) from None
        outputs.append({'model':name,'revision':model['revision'],'lines':len(result.lines)})
    return outputs
