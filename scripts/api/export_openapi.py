"""Export the existing customer contract; does not change API/server routes."""
import copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from maas_platform.schemas import TaskCreate,TaskView,ApiError
from maas_platform.consumer_ocr import ConsumerOCRRequest
from maas_platform.ocr_connector import OCRInput,OCRParameters,OCRResult

def obj(properties,required=(),**kw):return {'type':'object','properties':properties,'required':list(required),**kw}
def ref(n):return {'$ref':'#/components/schemas/'+n}
def string(description,**kw):return {'type':'string','description':description,**kw}
def response(description,schema):return {'description':description,'content':{'application/json':{'schema':schema}}}
S={}
for model in (TaskCreate,TaskView,ApiError,ConsumerOCRRequest,OCRInput,OCRParameters,OCRResult):
 schema=model.model_json_schema(ref_template='#/components/schemas/{model}');S.update(schema.pop('$defs',{}));S[model.__name__]=schema
notes={
'TaskCreate':{'model':'已授权逻辑模型名，当前ocr.default。','input':'模型输入，当前OCR支持image.artifact_id或image.data_url二选一；data_url为待部署的新实现，现网仍需先上传。','parameters':'当前OCR仅允许空对象。','timeout_seconds':'自受理开始的任务总期限，含排队；区别于模型单次运行预算。','max_attempts':'最多领取/尝试次数。默认3；OCR不可安全重放，设置3不意味着对未知远端活动自动重试。'},
'TaskView':{'id':'任务ID。','model':'逻辑模型名。','revision':'提交时绑定的模型版本。','state':'任务状态；HTTP200不代表任务成功。UNKNOWN是内部活动状态，不是任务state。','attempts':'领取次数，不一定等于真实GPU执行次数。','result_artifact_id':'成功结果文件ID，否则为null；需单独下载。','error_code':'失败原因代码，否则可为null。','deadline':'任务总期限。','created_at':'创建时间。','updated_at':'最近更新时间。'},
'ConsumerOCRRequest':{'model':'已授权逻辑模型名。','image_url':'公开HTTP(S) PNG/JPEG地址，与image_base64恰好一个；不支持私网、鉴权下载及重定向。','image_base64':'严格标准裸base64或PNG/JPEG data URL；与image_url恰好一个。','language':'仅null/auto/zh/en，提示语义，不强制过滤输出或切换模型。','return_boxes':'严格布尔值；false时省略polygon。','request_id':'业务关联ID，禁止控制字符；不是幂等键，不保证去重。'},
'OCRResult':{'text':'完整识别文字。','lines':'逐行文字、置信度及原图像素坐标。','elapsed_ms':'模型报告处理耗时，毫秒，不等于端到端时长。','node':'上游报告的处理节点标识，客户端不应依赖其格式。'},
'OCRLine':{'text':'识别文字。','score':'0至1置信度。','box':'原图像素坐标点数组。'},
'InlineImage':{'data_url':'严格PNG/JPEG base64 data URL，不接受裸base64、公开URL或其他字段。'},
'ImageReference':{'artifact_id':'预先上传的同project且未过期输入artifact ID。'},
'OCRInput':{'image':'单张图片：已有artifact_id引用，或PNG/JPEG base64 data_url；两个来源互斥。'},
'ApiError':{'error':'结构化错误。'},'ApiErrorDetail':{'code':'机器可读错误码。','message':'错误说明，不作为程序分支依据。','retryable':'错误的可重试提示，不授权重放完成状态未知的推理。','request_id':'服务器生成的关联ID。'}}
for n,schema in S.items():
 schema.setdefault('description',n+'（与当前实现一致）')
 for k,v in schema.get('properties',{}).items():v['description']=notes.get(n,{}).get(k,k+'字段，约束见schema。')
S['ConsumerOCRRequest']['oneOf']=[{'required':['image_url'],'properties':{'image_url':{'type':'string'},'image_base64':{'type':'null'}}},{'required':['image_base64'],'properties':{'image_base64':{'type':'string'},'image_url':{'type':'null'}}}]
S['ConsumerOCRRequest']['properties']['language']['enum']=[None,'auto','zh','en']
S['TaskCreate']['properties']['input']=ref('OCRInput');S['TaskCreate']['properties']['parameters']={**ref('OCRParameters'),'default':{},'description':notes['TaskCreate']['parameters']}
S['ArtifactCreated']=obj({'id':string('生成的artifact ID。',example='art_example'),'size':{'type':'integer','minimum':1,'description':'文件字节数。'},'sha256':string('原始文件SHA256。',pattern='^[a-f0-9]{64}$')},['id','size','sha256'])
S['ConsumerLine']=obj({'text':string('识别文字。'),'confidence':{'type':'number','minimum':0,'maximum':1,'description':'可选置信度，上游未提供时省略。'},'polygon':{'type':'array','minItems':4,'maxItems':4,'description':'可选四边形，原图像素坐标；return_boxes=false时省略。','items':{'type':'array','minItems':2,'maxItems':2,'items':{'type':'number'}}}},['text'])
S['ConsumerResult']=obj({'lines':{'type':'array','items':ref('ConsumerLine'),'description':'无文字时为空数组，仍为成功。'}},['lines'])
S['TaskResult']=obj({'outputs':ref('OCRResult'),'model_revision':string('执行版本。'),'attempt':{'type':'integer','minimum':1,'description':'产出结果的尝试编号。'},'usage':obj({'unit':{'const':'task','description':'计量单位。'},'quantity':{'const':1,'description':'成功任务数量。'}},['unit','quantity'])},['outputs','model_revision','attempt','usage'])
S['Capabilities']=obj({'model':string('逻辑模型名。'),'revision':string('当前登记版本。'),'execution_modes':{'type':'array','items':{'const':'async'},'description':'当前实际返回仅async；不表示系统没有独立同步接口。'},'max_runtime_s':{'type':'integer','description':'模型目录单次执行预算，当前OCR25秒，以运行配置为准。'},'input_schema':{'type':'object','additionalProperties':True,'description':'当前模型输入JSON Schema。'},'parameters_schema':{'type':'object','additionalProperties':True,'description':'模型参数JSON Schema，当前OCR为空对象。'}},['model','revision','execution_modes','max_runtime_s','input_schema','parameters_schema'])
S['ChatRequest']=obj({'model':string('逻辑模型名。',example='ocr.default'),'stream':{'type':'boolean','const':False,'default':False,'description':'仅非流式。'},'messages':{'type':'array','description':'当前受支持形式：user消息包含单张PNG/JPEG base64 data URL；其余结构由上游校验。','items':obj({'role':string('消息角色。',example='user'),'content':{'type':'array','items':obj({'type':{'const':'image_url'},'image_url':obj({'url':string('PNG/JPEG base64 data URL。',example='data:image/png;base64,...')},['url'])},['type','image_url'])}},['role','content'])}},['model','messages'],additionalProperties=True)
S['ChatResponse']=obj({'choices':{'type':'array','description':'上游Chat响应；通常读取首个choice。','items':obj({'message':obj({'content':string('JSON编码的OCRResult字符串；需要第二次JSON解析。'),'role':string('通常assistant。')},['content'])},['message'])}},['choices'],additionalProperties=True)
errors={'400':'请求无效或缺少Idempotency-Key（IDEMPOTENCY_REQUIRED）；不保证所有400都使用相同码。','401':'缺少或未登记的Bearer凭据。','403':'凭据撤销、模型无权限或上游拒绝。','404':'资源不存在/跨project不可见，或模型未登记。','408':'图片下载/准备超时（若适用）。','409':'幂等键已被不同请求内容使用。','410':'文件已过期或被清理（RESULT_EXPIRED）。','413':'请求体、文件或像素超限；代理层可能返回HTML。','415':'不支持的媒体类型。','422':'请求schema、图片、模型参数或上游校验不通过。','429':'队列、存储配额或同步并发容量已满。','502':'输出无效/超限、图片获取失败或上游异常（若适用）。','503':'服务/鉴权不可用、维护暂停或未知后台活动隔离。','504':'入口等待上游超时，可能是非JSON响应；不可自动重放。'}
headers={'X-Request-ID':{'description':'服务器生成的请求关联ID；入口直接拒绝时可能没有。','schema':{'type':'string'}}}
P={}
def add(path,method,operation,summary,description,tag,success,body=None,params=(),codes=()):
 op={'operationId':operation,'summary':summary,'description':description,'tags':[tag],'parameters':list(params),'responses':{}}
 for status,(desc,schema) in success.items():op['responses'][str(status)]=response(desc,schema)
 for code in set(['401','403','413','422','503','504',*codes]):op['responses'][code]=response(errors[code],ref('ApiError'))
 for r in op['responses'].values():r['headers']=copy.deepcopy(headers)
 if body:op['requestBody']=body
 P.setdefault(path,{})[method]=op
jsonbody=lambda n:{'required':True,'content':{'application/json':{'schema':ref(n)}}}
ident={'name':'ident','in':'path','required':True,'description':'同project任务或文件ID，依路径类型确定。','schema':{'type':'string'}}
for path,oid in [('/ocr','ocr'),('/v1/ocr','ocrV1')]:add(path,'post',oid,'同步OCR（消费者兼容）','单张PNG/JPEG，decoded大小默认≤10MiB，像素≤20000000。URL和base64恰好一个；无持久同步幂等，request_id仅关联。返回顶层lines，不保存可查询任务。', '同步OCR',{200:('识别成功；空lines为无文字。',ref('ConsumerResult'))},jsonbody('ConsumerOCRRequest'),codes=['404','408','415','429','502'])
add('/v1/chat/completions','post','chatOCR','同步OCR（Chat格式）','返回上游Chat JSON；choices[0].message.content为OCRResult的JSON字符串。仅stream=false，不支持SSE；不提供持久去重。传输完成状态不明时不可自动重发。','同步OCR',{200:('非流式Chat响应。',ref('ChatResponse'))},jsonbody('ChatRequest'),codes=['400','404','429','502'])
upload={'required':True,'description':'原始非空字节，不是JSON对象、base64或multipart。默认≤10485760字节。允许的非图片类型不代表OCR可处理。','content':{m:{'schema':{'type':'string','format':'binary'}} for m in ['image/png','image/jpeg','text/plain','application/json','application/octet-stream']}}
add('/maas/v1/artifacts','post','uploadArtifact','上传输入文件','保存到当前project，默认24小时保留。返回ID/字节数/哈希，无公开URL及expires_at；上传并不保证图片可推理，无上传幂等。','文件',{201:('文件已保存。',ref('ArtifactCreated'))},upload,codes=['415','429'])
add('/maas/v1/artifacts/{ident}','get','downloadArtifact','下载输入或结果文件','需同project且文件可用。成功任务仍需另行下载；过期/清理可返回410，跨project或未知ID404。结果JSON包含outputs及版本、尝试、计量。','文件',{200:('返回原始输入字节或结果JSON。',ref('TaskResult'))},params=[ident],codes=['404','410'])
r=P['/maas/v1/artifacts/{ident}']['get']['responses']['200'];r['content'].update({m:{'schema':{'type':'string','format':'binary'}} for m in ['image/png','image/jpeg','text/plain','application/octet-stream']});r['content']['application/json']['schema']={'anyOf':[ref('TaskResult'),{}],'description':'结果为TaskResult；上传的application/json输入按原始内容返回，不限于该schema。'};r['headers'].update({'Cache-Control':{'schema':{'const':'no-store'}},'Content-Disposition':{'schema':{'const':'attachment'}}})
idem={'name':'Idempotency-Key','in':'header','required':True,'description':'project范围持久幂等键。同键且同请求返回当前原任务；不同内容409。新artifact ID视为不同内容。不得换键重做完成不明推理。','schema':{'type':'string','minLength':1,'maxLength':128}}
add('/maas/v1/tasks','post','createTask','提交异步OCR任务','已有artifact引用方式保持；新增image.data_url（严格PNG/JPEG base64 data URL）与artifact_id二选一。本地新实现，现网尚未部署。引用请求仍≤65536字节；内嵌请求默认≤20MiB，解码图片≤10MiB且≤20MP。内部原子登记输入及任务，幂等复用不重复保存输入。新任务202、幂等复用200；受理只表示入队。维护暂停时仍可能入队；等待队列满429不创建任务。','异步任务',{202:('新任务已受理。',ref('TaskView')),200:('返回已有幂等任务的当前状态。',ref('TaskView'))},jsonbody('TaskCreate'),[idem],['400','404','409','429'])
for status in ['200','202']:P['/maas/v1/tasks']['post']['responses'][status]['headers']['Location']={'description':'任务查询相对路径。','schema':{'type':'string'}}
add('/maas/v1/tasks/{ident}','get','getTask','查询任务','返回任务状态和结果文件ID，不内嵌结果。不提供列表、webhook或SSE。建议1–2秒有界轮询。任务终态不证明远端GPU活动已结束；UNKNOWN可能表现为FAILED/error_code=UNKNOWN_BACKEND_ACTIVITY。当前无输入/结果文件生命周期摘要。','异步任务',{200:('当前任务状态，需检查state。',ref('TaskView'))},params=[ident],codes=['404'])
add('/maas/v1/tasks/{ident}/cancel','post','cancelTask','取消或请求取消任务','无需请求体。QUEUED/RETRY_WAIT→CANCELLED，RUNNING→CANCEL_REQUESTED，终态保持；重复调用不创建任务。不保证立即终止GPU活动。','异步任务',{200:('更新后或已有终态任务。',ref('TaskView'))},params=[ident],codes=['404'])
add('/maas/v1/models/{name}/capabilities','get','modelCapabilities','查询已授权模型能力','不是实时推理健康检查。当前execution_modes实际只列async，同步入口仍可独立使用；未知模型404、无权限403。没有公开模型列表接口。','模型能力',{200:('当前登记的模型版本、预算及输入/参数schema。',ref('Capabilities'))},params=[{'name':'name','in':'path','required':True,'description':'已登记逻辑模型名，当前ocr.default；不是GPUStack内部部署名。','schema':{'type':'string','example':'ocr.default'}}],codes=['404'])
doc={'openapi':'3.1.0','info':{'title':'Cybercafe MaaS 对外OCR API','version':'2026-11-02','description':'代码契约：2026-11-02新增内嵌任务图片已实现/待部署；现网仍使用artifact引用，详见INLINE-TASK-IMAGE。其余为既有客户接口。所有操作要求管理员登记且具有相应权限的Bearer推理key；资源按project隔离，客户端身份头不选择project。URL不支持query参数。当前入口≤20MiB、同步JSON默认≤20MiB、异步引用JSON≤64KiB、内嵌任务JSON≤20MiB（新代码，待部署）、原图≤10MiB；入口错误可能返回HTML/plain text而非ApiError。当前运行配置8副本/8执行器、执行并发8/8、等待队列25/25，均可调整且不构成SLA。单次模型运行预算与总任务期限不同。未开放/v1/models、/healthz、管理API、Swagger UI或/openapi.json；此文件用于Swagger Editor/UI离线或自行托管导入。内嵌提交增量尚未发布；部署前不能在现网使用。'},'servers':[{'url':'http://{host}:{port}','description':'受控可达HTTP入口；host需由运维提供，容器回环地址不代表宿主机。','variables':{'host':{'default':'localhost'},'port':{'default':'18831'}}},{'url':'https://{host}:{port}','description':'私有TLS入口，需信任运维提供的证书。','variables':{'host':{'default':'localhost'},'port':{'default':'18832'}}}],'security':[{'BearerAuth':[]}],'tags':[{'name':n,'description':d} for n,d in [('同步OCR','同步完成并返回识别结果。'),('文件','上传和下载project隔离的输入/结果。'),('异步任务','受理、查询和取消持久任务。'),('模型能力','查询已登记及获授权能力，不代表实时GPU健康。')]],'paths':P,'components':{'securitySchemes':{'BearerAuth':{'type':'http','scheme':'bearer','description':'管理员登记于MaaS且GPUStack有效、有模型权限的推理key；不是任意GPUStack key或管理key。'}},'schemas':S}}
S['TaskCreate']['examples']=[{'model':'ocr.default','input':{'image':{'artifact_id':'art_example'}},'parameters':{},'timeout_seconds':120,'max_attempts':1}]
inline_example={'model':'ocr.default','input':{'image':{'data_url':'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAE0lEQVR4nGP8//8/AwwwwVnoHABvLQMDNeDq7AAAAABJRU5ErkJggg=='}},'parameters':{},'timeout_seconds':120,'max_attempts':1}
S['TaskCreate']['examples'].append(inline_example)
P['/maas/v1/tasks']['post']['requestBody']['content']['application/json']['examples']={
 'artifactReference':{'summary':'方式一：引用已上传图片','description':'先上传图片，将artifact_id替换为返回的真实ID。','value':S['TaskCreate']['examples'][0]},
 'inlineDataUrl':{'summary':'方式二：data_url内嵌PNG（新代码，待部署）','description':'有效4×3白色PNG示例，识别结果可能为空；实际OCR请替换为自己的PNG/JPEG完整base64 data URL。须设置Authorization和Idempotency-Key，与artifact_id互斥。现网尚未发布此能力。','value':inline_example}}
S['ConsumerOCRRequest']['examples']=[{'model':'ocr.default','image_base64':'<PNG/JPEG base64>','return_boxes':True}]
S['ConsumerResult']['examples']=[{'lines':[{'text':'中文 English','confidence':0.99,'polygon':[[0,0],[100,0],[100,20],[0,20]]}]}]
for path_item in P.values():
 for op in path_item.values():
  for code in ('413','504'):
   op['responses'][code]['content']['text/html']={'schema':{'type':'string'},'example':'<html>Gateway error</html>'}
path=ROOT/'docs/api/openapi.json';path.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
print('Exported',len(P),'paths',sum(len(v) for v in P.values()),'operations')
