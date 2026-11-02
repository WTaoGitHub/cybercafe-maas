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
'TaskCreate':{'model':'已授权逻辑模型名，当前ocr.default。','input':'模型输入，当前OCR支持artifact_id、image_base64、image_url已部署，三个来源互斥，保留旧别名兼容，三个来源互斥。','parameters':'当前OCR仅允许空对象。','timeout_seconds':'自受理开始的任务总期限，含排队；区别于模型单次运行预算。','max_attempts':'最多领取/尝试次数。默认3；OCR不可安全重放，设置3不意味着对未知远端活动自动重试。'},
'TaskView':{'id':'任务ID。','model':'逻辑模型名。','revision':'提交时绑定的模型版本。','state':'任务状态；HTTP200不代表任务成功。UNKNOWN是内部活动状态，不是任务state。','attempts':'领取次数，不一定等于真实GPU执行次数。','result_artifact_id':'成功结果文件ID，否则为null；需单独下载。','error_code':'失败原因代码，否则可为null。','deadline':'任务总期限。','created_at':'创建时间。','updated_at':'最近更新时间。'},
'ConsumerOCRRequest':{'model':'已授权逻辑模型名。','image_url':'公开HTTP(S) PNG/JPEG地址，与image_base64恰好一个；不支持私网、鉴权下载及重定向。','image_base64':'严格标准裸base64或PNG/JPEG data URL；与image_url恰好一个。','language':'仅null/auto/zh/en，提示语义，不强制过滤输出或切换模型。','return_boxes':'严格布尔值；false时省略polygon。','request_id':'业务关联ID，禁止控制字符；不是幂等键，不保证去重。'},
'OCRResult':{'text':'完整识别文字。','lines':'逐行文字、置信度及原图像素坐标。','elapsed_ms':'模型报告处理耗时，毫秒，不等于端到端时长。','node':'上游报告的处理节点标识，客户端不应依赖其格式。'},
'OCRLine':{'text':'识别文字。','score':'0至1置信度。','box':'原图像素坐标点数组。'},
'URLImage':{'image_url':'公开HTTP(S) PNG/JPEG地址，最长4096字符；202前安全下载并保存，image_url已部署。'},
'InlineImage':{'image_base64':'严格PNG/JPEG裸base64或data URL；与artifact_id/image_url互斥。旧data_url为弃用别名，不与新字段同时提供。'},
'ImageReference':{'artifact_id':'预先上传的同project且未过期输入artifact ID。'},
'OCRInput':{'image':'单张图片：已有artifact_id引用、image_base64裸base64/data URL或公开image_url（三选一，image_url已部署）。'},
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
S['TokenUsage']=obj({'status':{'type':'string','enum':['available','unavailable'],'description':'仅完整有效的上游token报告为available；不可用不能按0或免费处理。'},'input_tokens':{'type':['integer','null'],'minimum':0,'description':'上游prompt_tokens，缺失/无效为null。'},'output_tokens':{'type':['integer','null'],'minimum':0,'description':'上游completion_tokens，缺失/无效为null。'},'source':{'type':['string','null'],'enum':['upstream_reported',None],'description':'仅上游真实报告，不估算字符/像素/base64。'},'reason':{'type':['string','null'],'enum':['UPSTREAM_USAGE_ABSENT','UPSTREAM_USAGE_INVALID',None],'description':'不可用原因，available时null。'}},['status','input_tokens','output_tokens','source','reason'])
S['OCRUsage']=obj({'id':string('稳定的计量ID，下游应按此去重。'),'status':{'const':'final','description':'最终成功用量事实，不是收费交易状态。'},'source_type':{'type':'string','enum':['task','sync'],'description':'异步任务或同步调用。'},'source_id':string('task时为任务ID；sync时为本次同步调用的内部活动ID，不提供同步请求去重。'),'model':string('逻辑模型名。'),'revision':string('本次执行的服务版本。'),'successful_images':{'type':'integer','const':1,'description':'当前单图OCR成功处理1张，空文字结果仍可为成功。'},'tokens':ref('TokenUsage'),'recorded_at':{'type':'string','format':'date-time','description':'收据构建时间，非GPU运行时间或数据库精确提交时刻。'}},['id','status','source_type','source_id','model','revision','successful_images','tokens','recorded_at'])
S['TaskResult']['properties']['metering']={**ref('OCRUsage'),'description':'E16已部署OCR最终用量；历史结果或非OCR模型可没有此字段。原usage任务计量保持。'}
S['OCRUsage']['examples']=[{'id':'usage_task_task_example','status':'final','source_type':'task','source_id':'task_example','model':'ocr.default','revision':'example-r1','successful_images':1,'tokens':{'status':'unavailable','input_tokens':None,'output_tokens':None,'source':None,'reason':'UPSTREAM_USAGE_ABSENT'},'recorded_at':'2026-11-02T00:00:00Z'}]
S['ChatRequest']=obj({'model':string('逻辑模型名。',example='ocr.default'),'stream':{'type':'boolean','const':False,'default':False,'description':'仅非流式。'},'messages':{'type':'array','description':'当前受支持形式：user消息包含单张PNG/JPEG base64 data URL；其余结构由上游校验。','items':obj({'role':string('消息角色。',example='user'),'content':{'type':'array','items':obj({'type':{'const':'image_url'},'image_url':obj({'url':string('PNG/JPEG base64 data URL。',example='data:image/png;base64,...')},['url'])},['type','image_url'])}},['role','content'])}},['model','messages'],additionalProperties=True)
S['ChatResponse']=obj({'choices':{'type':'array','description':'上游Chat响应；通常读取首个choice。','items':obj({'message':obj({'content':string('JSON编码的OCRResult字符串；需要第二次JSON解析。'),'role':string('通常assistant。')},['content'])},['message'])}},['choices'],additionalProperties=True)
errors={'400':'请求无效或缺少Idempotency-Key（IDEMPOTENCY_REQUIRED）；不保证所有400都使用相同码。','401':'缺少或未登记的Bearer凭据。','403':'凭据撤销、模型无权限或上游拒绝。','404':'资源不存在/跨project不可见，或模型未登记。','408':'图片下载/准备超时（若适用）。','409':'幂等键已被不同请求内容使用。','410':'文件已过期或被清理（RESULT_EXPIRED）。','413':'请求体、文件或像素超限；代理层可能返回HTML。','415':'不支持的媒体类型。','422':'请求schema、图片、模型参数或上游校验不通过。','429':'队列、存储配额或同步并发容量已满。','502':'输出无效/超限、图片获取失败或上游异常（若适用）。','503':'服务/鉴权不可用、维护暂停或未知后台活动隔离。','504':'入口等待上游超时，可能是非JSON响应；不可自动重放。'}
headers={'X-Request-ID':{'description':'服务器生成的请求关联ID；入口直接拒绝时可能没有。','schema':{'type':'string'}}}
P={}
def add(path,method,operation,summary,description,tag,success,body=None,params=(),codes=()):
 op={'operationId':operation,'summary':summary,'description':description,'tags':[tag],'parameters':list(params),'responses':{}}
 for status,(desc,schema) in success.items():op['responses'][str(status)]=response(desc,schema)
 for code in sorted(set(['401','403','413','422','503','504',*codes])):op['responses'][code]=response(errors[code],ref('ApiError'))
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
idem={'name':'Idempotency-Key','in':'header','required':True,'description':'project范围持久幂等键。示例值仅演示：每次新业务操作生成新UUID并保存，重试同一操作沿用原值和原body，不能反复用示例值提交不同任务。同键且同请求返回当前原任务；不同内容409。新artifact ID视为不同内容。不得换键重做完成不明推理。','schema':{'type':'string','minLength':1,'maxLength':128},'example':'ocr-550e8400-e29b-41d4-a716-446655440000'}
add('/maas/v1/tasks','post','createTask','提交异步OCR任务','input.image支持artifact_id、image_base64或image_url三选一。三种规范输入已部署，兼容旧data_url/url别名。新版本保留data_url/url为弃用别名，新旧别名不可同时提供。image_base64接受严格PNG/JPEG裸base64或data URL。URL下载在202前完成，整体10秒，沿用公网限制，失败不创建任务；下载无持久任务ID。已受理同键不重下载，同键正在下载返回409/IDEMPOTENCY_IN_PROGRESS；下载容量全局4、每project2，满额429/IMAGE_FETCH_CAPACITY。无全局锁网络等待；同URL新key可获取不同内容。引用及URL请求仍≤65536字节；内嵌请求默认≤20MiB，解码图片≤10MiB且≤20MP。内部原子登记输入及任务，幂等复用不重复保存输入。新任务202、幂等复用200；受理只表示入队。维护暂停时仍可能入队；等待队列满429不创建任务。','异步任务',{202:('新任务已受理。',ref('TaskView')),200:('返回已有幂等任务的当前状态。',ref('TaskView'))},jsonbody('TaskCreate'),[idem],['400','404','408','409','415','429'])
for status in ['200','202']:P['/maas/v1/tasks']['post']['responses'][status]['headers']['Location']={'description':'任务查询相对路径。','schema':{'type':'string'}}
add('/maas/v1/tasks/{ident}','get','getTask','查询任务','返回任务状态和结果文件ID，不内嵌结果。不提供列表、webhook或SSE。建议1–2秒有界轮询。任务终态不证明远端GPU活动已结束；UNKNOWN可能表现为FAILED/error_code=UNKNOWN_BACKEND_ACTIVITY。当前无输入/结果文件生命周期摘要。','异步任务',{200:('当前任务状态，需检查state。',ref('TaskView'))},params=[ident],codes=['404'])
add('/maas/v1/tasks/{ident}/cancel','post','cancelTask','取消或请求取消任务','无需请求体。QUEUED/RETRY_WAIT→CANCELLED，RUNNING→CANCEL_REQUESTED，终态保持；重复调用不创建任务。不保证立即终止GPU活动。','异步任务',{200:('更新后或已有终态任务。',ref('TaskView'))},params=[ident],codes=['404'])
add('/maas/v1/models/{name}/capabilities','get','modelCapabilities','查询已授权模型能力','不是实时推理健康检查。当前execution_modes实际只列async，同步入口仍可独立使用；未知模型404、无权限403。没有公开模型列表接口。','模型能力',{200:('当前登记的模型版本、预算及输入/参数schema。',ref('Capabilities'))},params=[{'name':'name','in':'path','required':True,'description':'已登记逻辑模型名，当前ocr.default；不是GPUStack内部部署名。','schema':{'type':'string','example':'ocr.default'}}],codes=['404'])
add('/maas/v1/usage/{ident}','get','getOCRUsage','查询OCR最终用量收据','E16已部署并通过真实OCR验收。使用同步响应X-Usage-ID或异步结果metering.id，按已知ID查询。仅同project；与artifact TTL独立保存，无自动清理/分页列表/价格。不可用404/USAGE_NOT_AVAILABLE；当前PP-OCRv4没有tokens报告，tokens为null及unavailable，不得按tokens结算。','计量',{200:('成功OCR的最终用量事实。',ref('OCRUsage'))},params=[{'name':'ident','in':'path','required':True,'description':'usage_开头的计量ID，不是artifact ID。','schema':{'type':'string'}}],codes=['404'])
add('/maas/v1/tasks/{ident}/usage','get','getTaskOCRUsage','按任务查询OCR最终用量','E16已部署并通过真实OCR验收。无需先下载结果即可查询；待执行、失败、取消或旧任务无新账本时404/USAGE_NOT_AVAILABLE，不回填推算。跨project任务404，文件过期不删除已有收据。','计量',{200:('此任务唯一的最终成功OCR用量。',ref('OCRUsage'))},params=[ident],codes=['404'])
for path in ('/maas/v1/usage/{ident}','/maas/v1/tasks/{ident}/usage'):
 P[path]['get']['responses']['200']['headers']['Cache-Control']={'schema':{'const':'no-store'},'description':'避免缓存客户用量数据。'}
for path in ('/ocr','/v1/ocr','/v1/chat/completions'):
 P[path]['post']['responses']['200']['headers']['X-Usage-ID']={'description':'E16已部署：已确认的单图OCR成功时提供，GET /maas/v1/usage/{ident}查询。内部探针、非单图/无法确认OCR输出的旧Chat透传没有该头；无同步持久去重，重复调用可能产生新用量。','schema':{'type':'string'}}
doc={'openapi':'3.1.0','info':{'title':'Cybercafe MaaS 对外OCR API','version':'2026-11-02','description':'2026-11-02现网契约：artifact_id/image_base64/image_url三种输入、E16成功图片计量收据及两个查询接口均已部署并完成真实OCR验收。当前PP-OCRv4无真实token报告，tokens实际为null/unavailable。所有操作要求管理员登记且具有相应权限的Bearer推理key；资源按project隔离，客户端身份头不选择project。MaaS API路径不支持query参数；下载的图片URL可有查询参数，但不得包含用户密码或片段。当前入口≤20MiB、同步JSON默认≤20MiB、异步引用JSON≤64KiB、内嵌任务JSON≤20MiB、原图≤10MiB；入口错误可能返回HTML/plain text而非ApiError。当前运行配置8副本/8执行器、执行并发8/8、等待队列25/25，均可调整且不构成SLA。单次模型运行预算与总任务期限不同。未开放/v1/models、/healthz、管理API、Swagger UI或/openapi.json；此文件用于Swagger Editor/UI离线或自行托管导入。内嵌提交已发布，保留原artifact引用兼容。'},'servers':[{'url':'http://{host}:{port}','description':'HTTP：本机/授权SSH转发或受控容器入口。localhost仅指当前调用环境；跨容器须替换host为运维提供的宿主机地址。','variables':{'host':{'default':'localhost'},'port':{'default':'18831'}}},{'url':'https://{host}:{port}','description':'HTTPS：本机或授权SSH转发的私有TLS入口；须使用正确host并信任运维提供的证书，不默认绕过校验。','variables':{'host':{'default':'localhost'},'port':{'default':'18832'}}}],'security':[{'BearerAuth':[]}],'tags':[{'name':n,'description':d} for n,d in [('同步OCR','同步完成并返回识别结果。'),('文件','上传和下载project隔离的输入/结果。'),('异步任务','受理、查询和取消持久任务。'),('模型能力','查询已登记及获授权能力，不代表实时GPU健康。'),('计量','成功图片与真实上游token用量事实，价格及收费归下游。')]],'paths':P,'components':{'securitySchemes':{'BearerAuth':{'type':'http','scheme':'bearer','description':'在Swagger Authorize或VSCode插件的HTTP Bearer认证设置中填入管理员登记的推理key（通常只填key，工具添加Bearer前缀）。生成curl后检查是否包含Authorization: Bearer；缺失时手动添加。不要把真实key保存进OpenAPI或示例。该key须在GPUStack有效且有模型权限，不是管理key。'}},'schemas':S}}
S['TaskCreate']['examples']=[{'model':'ocr.default','input':{'image':{'artifact_id':'art_REPLACE_WITH_UPLOADED_ID'}},'parameters':{},'timeout_seconds':120,'max_attempts':1}]
inline_example={'model':'ocr.default','input':{'image':{'image_base64':'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAE0lEQVR4nGP8//8/AwwwwVnoHABvLQMDNeDq7AAAAABJRU5ErkJggg=='}},'parameters':{},'timeout_seconds':120,'max_attempts':1}
S['TaskCreate']['examples'].append(inline_example)
P['/maas/v1/tasks']['post']['requestBody']['content']['application/json']['examples']={
 'artifactReference':{'summary':'方式一：引用已上传图片','description':'先POST /maas/v1/artifacts上传图片，将art_REPLACE_WITH_UPLOADED_ID替换为响应id；不能直接提交占位符。','value':S['TaskCreate']['examples'][0]},
 'inlineBase64':{'summary':'方式二：image_base64内嵌PNG','description':'有效4×3白色PNG示例，识别结果可能为空；实际OCR请替换为自己的PNG/JPEG完整base64 data URL。须设置Authorization和Idempotency-Key，与artifact_id互斥。本示例的新名称已发布；旧data_url仅保留兼容。','value':inline_example}}
P['/maas/v1/tasks']['post']['description']+='\n\n生成curl前：选择正确Server，配置HTTP Bearer认证并检查Authorization头，填写新的Idempotency-Key；选择artifactReference时替换真实文件ID，inlineBase64可直接提交单张图片。Accept包含text/html仅表示可能遇到入口HTML错误；客户端须先检查HTTP状态和Content-Type。'
P['/maas/v1/tasks']['post']['x-codeSamples']=[{'lang': 'Shell', 'label': 'curl：已上传图片引用', 'source': '# 设置MAAS_BASE_URL、MAAS_API_KEY，并替换下面的真实artifact ID。\n# 新操作仅生成一次并保存；同次重试沿用原键和原JSON。\nIDEMPOTENCY_KEY="$(python3 -c \'import uuid; print(uuid.uuid4())\')"\ncurl -i --request POST \\\n  --url "$MAAS_BASE_URL/maas/v1/tasks" \\\n  --header "Authorization: Bearer $MAAS_API_KEY" \\\n  --header \'Accept: application/json\' \\\n  --header \'Content-Type: application/json\' \\\n  --header "Idempotency-Key: $IDEMPOTENCY_KEY" \\\n  --data \'{\n    "model": "ocr.default",\n    "input": {\n      "image": {\n        "artifact_id": "art_REPLACE_WITH_UPLOADED_ID"\n      }\n    },\n    "parameters": {},\n    "timeout_seconds": 120,\n    "max_attempts": 1\n  }\'\n'}]
inline_curl='# image_base64规范字段已部署；兼容旧data_url。\n# 此处PNG为有效4×3白色图片；真实识别请替换为自己的完整PNG/JPEG data URL。\n# 先设置MAAS_BASE_URL、MAAS_API_KEY；同次重试保持原幂等键和原JSON。\nIDEMPOTENCY_KEY="$(python3 -c \'import uuid; print(uuid.uuid4())\')"\ncurl -i --request POST \\\n  --url "$MAAS_BASE_URL/maas/v1/tasks" \\\n  --header "Authorization: Bearer $MAAS_API_KEY" \\\n  --header \'Accept: application/json\' \\\n  --header \'Content-Type: application/json\' \\\n  --header "Idempotency-Key: $IDEMPOTENCY_KEY" \\\n  --data \'INLINE_JSON_PLACEHOLDER\'\n'.replace('INLINE_JSON_PLACEHOLDER',json.dumps(inline_example,ensure_ascii=False,indent=2))
P['/maas/v1/tasks']['post']['x-codeSamples'].append({'lang':'Shell','label':'curl：image_base64内嵌图片','source':inline_curl})
url_example={'model':'ocr.default','input':{'image':{'image_url':'https://example.org/replace-with-public-image.png'}},'parameters':{},'timeout_seconds':120,'max_attempts':1}
S['TaskCreate']['examples'].append(url_example)
P['/maas/v1/tasks']['post']['requestBody']['content']['application/json']['examples']['remoteImageUrl']={'summary':'方式三：公网图片URL','description':'替换为MaaS服务器可访问的公开PNG/JPEG地址；示例域名仅占位。无登录/重定向/私网，MaaS下载后才返回202，不是传给worker临时读取。','value':url_example}
P['/maas/v1/tasks']['post']['responses']['409']['description']='IDEMPOTENCY_CONFLICT：同键不同内容；或IDEMPOTENCY_IN_PROGRESS：同键正在下载准备，稍后用原键及原body重试。'
url_curl=inline_curl[:inline_curl.index('  --data ')]+'  --data '+"'"+json.dumps(url_example,ensure_ascii=False,indent=2)+"'"+'\n'
url_curl=url_curl.replace('# image_base64规范字段已部署；兼容旧data_url。','# 异步image_url已部署；下载完成后才返回202。').replace('# 此处PNG为有效4×3白色图片；真实识别请替换为自己的完整PNG/JPEG data URL。','# 替换为服务器可下载的公开PNG/JPEG地址。')
P['/maas/v1/tasks']['post']['x-codeSamples'].append({'lang':'Shell','label':'curl：远程图片URL','source':url_curl})
S['ConsumerOCRRequest']['examples']=[{'model':'ocr.default','image_base64':'<PNG/JPEG base64>','return_boxes':True}]
S['ConsumerResult']['examples']=[{'lines':[{'text':'中文 English','confidence':0.99,'polygon':[[0,0],[100,0],[100,20],[0,20]]}]}]
for path_item in P.values():
 for op in path_item.values():
  for code in ('413','504'):
   op['responses'][code]['content']['text/html']={'schema':{'type':'string'},'example':'<html>Gateway error</html>'}
# Real captured responses are checked in after redacting node identities only.
real_file=ROOT/'docs/api/examples/real-responses-20261102.json'
if real_file.exists():
 real=json.loads(real_file.read_text())['responses']
 def actual(path,method,status,key,label,description):
  op=P[path][method];target=op['responses'][str(status)]
  media=target['content']['application/json'];media.pop('example',None)
  media.setdefault('examples',{})[label]={'summary':description,'description':'2026-11-02真实验收响应；自建文字图片，节点标识已脱敏。ID和时间为历史测试值，不可当成可复用输入。','value':real[key]['body']}
  for name,header in target.get('headers',{}).items():
   if name.lower() in real[key]['headers']:header['example']=real[key]['headers'][name.lower()]
 for path,method,status,key,label,description in [
  ('/ocr','post',200,'syncOCR','realOCR','真实同步OCR成功'),
  ('/v1/ocr','post',200,'syncOCRV1','realOCR','真实v1同步OCR成功'),
  ('/v1/chat/completions','post',200,'chatOCR','realChatOCR','真实Chat OCR成功，content仍是JSON字符串'),
  ('/maas/v1/artifacts','post',201,'uploadArtifact','realUpload','真实图片上传响应'),
  ('/maas/v1/artifacts/{ident}','get',200,'resultArtifact','realOCRResult','真实OCR结果文件及计量'),
  ('/maas/v1/tasks','post',202,'createTaskInline','realInlineAccepted','真实image_base64受理'),
  ('/maas/v1/tasks','post',202,'createTaskRemote','realURLAccepted','真实公网image_url下载后受理'),
  ('/maas/v1/tasks','post',202,'createTaskReference','realReferenceAccepted','真实artifact_id受理'),
  ('/maas/v1/tasks','post',200,'idempotentTask','realIdempotent','真实幂等复用已成功任务'),
  ('/maas/v1/tasks','post',409,'idempotencyConflict','realConflict','真实同键不同内容冲突'),
  ('/maas/v1/tasks','post',422,'sourceConflict','realSourceConflict','真实图片来源互斥错误'),
  ('/maas/v1/tasks','post',422,'privateURL','realBlockedURL','真实私网图片URL拒绝'),
  ('/maas/v1/tasks/{ident}','get',200,'getTask','realSucceeded','真实任务完成状态'),
  ('/maas/v1/tasks/{ident}/cancel','post',200,'cancelCompletedTask','realAlreadySucceeded','真实取消已成功任务，终态保持SUCCEEDED'),
  ('/maas/v1/models/{name}/capabilities','get',200,'capabilities','realCapabilities','真实模型能力及输入schema'),
  ('/maas/v1/usage/{ident}','get',200,'getUsage','realAsyncUsage','真实异步用量，tokens不可用'),
  ('/maas/v1/usage/{ident}','get',200,'syncUsage','realSyncUsage','真实同步用量，tokens不可用'),
  ('/maas/v1/tasks/{ident}/usage','get',200,'taskUsage','realTaskUsage','真实按任务查询用量')]:actual(path,method,status,key,label,description)
 S['OCRUsage']['examples']=[real['getUsage']['body'],real['syncUsage']['body']]
 S['ConsumerResult']['examples']=[real['syncOCR']['body']]
 S['ArtifactCreated']['examples']=[real['uploadArtifact']['body']]
 S['TaskResult']['examples']=[real['resultArtifact']['body']]
 S['OCRResult']['examples']=[real['resultArtifact']['body']['outputs']]
 S['TaskView']['examples']=[real['getTask']['body']]
 S['TokenUsage']['examples']=[real['getUsage']['body']['tokens']]
 doc['info']['description']+=' 成功及部分错误响应示例取自2026-11-02真实验收，自建文字测试图与默认白图请求不同；节点已脱敏，历史测试ID不可复用，不包含凭据或真实连接地址。'
path=ROOT/'docs/api/openapi.json';path.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
print('Exported',len(P),'paths',sum(len(v) for v in P.values()),'operations')
