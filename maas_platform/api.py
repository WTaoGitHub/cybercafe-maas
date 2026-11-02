import logging, uuid
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from .limits import JsonBodyLimit
from .adapters import adapter_for
from .auth import AuthConnector
from .config import Settings
from .errors import ServiceError
from .schemas import TaskCreate, TaskView
from .service import TaskService
from .sync_ingress import SyncIngress
from .consumer_ocr import ConsumerOCR,ConsumerOCRRequest


def create_app(settings=None, auth=None):
    settings = settings or Settings.from_env()
    service = TaskService(settings, auth or AuthConnector(settings))
    @asynccontextmanager
    async def lifespan(app):
        service.db.migrate(); yield
    app = FastAPI(title='MaaS persistent tasks', version='0.1.0', lifespan=lifespan)
    app.state.service = service
    app.state.sync_ingress=SyncIngress(service)
    app.add_middleware(JsonBodyLimit,sync_limit=settings.max_input_bytes*2,task_limit=max(65536,settings.max_input_bytes*2))

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'req_'+uuid.uuid4().hex
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({'error': {'code':exc.code,'message':exc.message,'retryable':exc.retryable,'request_id':request.state.request_id}},status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'error': {'code':'INPUT_INVALID','message':'Request does not match schema','retryable':False,'request_id':request.state.request_id}},status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        logging.getLogger('maas.api').error('request_unavailable request=%s',request.state.request_id)
        return JSONResponse({'error': {'code':'SERVICE_UNAVAILABLE','message':'Service temporarily unavailable','retryable':True,'request_id':request.state.request_id}},status_code=503)

    def identity(authorization: str = Header(default='')):
        key = authorization[7:] if authorization.startswith('Bearer ') else ''
        return service.auth.authenticate(key)

    @app.get('/healthz')
    def health():
        with service.db.transaction() as c: c.execute('SELECT 1')
        return {'ok':True,'version':'0.1.0','stage':'P3-controlled-ingress-pilot'}

    @app.post('/v1/chat/completions')
    async def synchronous(request:Request,who=Depends(identity)):
        try:body=await request.json()
        except ValueError:raise ServiceError('SYNC_INPUT','Request must be JSON',422) from None
        return await app.state.sync_ingress.infer(who,body)

    @app.post('/ocr')
    @app.post('/v1/ocr')
    async def consumer_ocr(body:ConsumerOCRRequest,request:Request,who=Depends(identity)):
        if body.request_id is not None:
            logging.getLogger('uvicorn.error').info('ocr request=%s consumer_request=%r',request.state.request_id,body.request_id)
        return await ConsumerOCR(app.state.sync_ingress).infer(who,body)

    @app.post('/maas/v1/artifacts', status_code=201)
    async def upload(request: Request, who=Depends(identity)):
        data=bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>settings.max_input_bytes:
                raise ServiceError('INPUT_SIZE','Input exceeds upload limit',413)
            data.extend(chunk)
        return service.upload(who,bytes(data),request.headers.get('content-type','application/octet-stream').split(';')[0])

    @app.get('/maas/v1/artifacts/{ident}')
    def download(ident: str, who=Depends(identity)):
        data,media=service.artifact(who,ident)
        return Response(data,media_type=media,headers={'Cache-Control':'no-store','Content-Disposition':'attachment'})

    @app.post('/maas/v1/tasks', status_code=202, response_model=TaskView)
    def submit(body:TaskCreate, request:Request, who=Depends(identity), idempotency_key:str=Header(default='')):
        row,created=service.submit(who,body,idempotency_key)
        return JSONResponse(jsonable_encoder(row),status_code=202 if created else 200,headers={'Location':'/maas/v1/tasks/'+row['id']})

    @app.get('/maas/v1/tasks/{ident}', response_model=TaskView)
    def get(ident:str,who=Depends(identity)): return service.get(who,ident)

    @app.post('/maas/v1/tasks/{ident}/cancel', response_model=TaskView)
    def cancel(ident:str,who=Depends(identity)): return service.cancel(who,ident)

    @app.get('/maas/v1/models/{name}/capabilities')
    def capability(name:str,who=Depends(identity)):
        model=service.model(name);service.auth.authorize(who,name,model)
        return {'model':name,'revision':model['revision'],'execution_modes':['async'],'max_runtime_s':model['max_runtime_s'],'input_schema':adapter_for(model['driver']).input_schema.model_json_schema(),'parameters_schema':adapter_for(model['driver']).parameter_schema.model_json_schema()}

    return app
