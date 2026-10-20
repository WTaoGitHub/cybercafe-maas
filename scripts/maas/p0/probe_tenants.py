"""Two disposable customer organizations consume platform-owned model routes."""
import json,os,subprocess,time
from pathlib import Path
from common import Resources,admin,request
root=Path('/root/wtaobj/maas-p0');res=Resources();out={'checks':{},'organization_pattern':'separate customer organizations consuming platform-owned routes','production_models_mutated':False}
try:
 orgs=[];users=[];ops=[];routes=[];keys=[];infos=[]
 for i in range(2):
  org=res.create('/v2/organizations',{'name':res.prefix+'-'+str(i),'display_name':res.prefix+' '+str(i)})
  u,op=res.user('tenant-'+str(i));admin('/v2/organizations/'+str(org['id'])+'/members',{'principal_ids':[u['id']],'role':'owner'})
  route=res.create('/v2/model-routes',{'name':res.prefix+'-route-'+str(i),'categories':['llm'],'access_policy':'allowed_principals','targets':[{'model_id':1,'weight':100}]})
  admin('/v2/model-routes/'+str(route['id'])+'/access',{'access_policy':'allowed_principals','principals':[{'principal_id':org['id'],'principal_type':'org'}]})
  info,key=res.key(op,'org-key-'+str(i),[route['name']],org['id'])
  assert info['owner_principal_id']==org['id']
  orgs.append(org);users.append(u);ops.append(op);routes.append(route);keys.append(key);infos.append(info)
 def check(label,key,model,headers=None):
  code,_,h=request('/token-auth',key=key,headers={'x-higress-llm-model':model,**(headers or {})});out['checks'][label]={'status':code};return code,h
 assert check('org_a_allowed',keys[0],routes[0]['name'])[0]==200
 assert check('org_b_allowed',keys[1],routes[1]['name'])[0]==200
 assert check('org_a_cross_denied',keys[0],routes[1]['name'])[0] in (403,404)
 assert check('org_b_cross_denied',keys[1],routes[0]['name'])[0] in (403,404)
 assert check('forged_org_cross_denied',keys[0],routes[1]['name'],{'X-Organization-Id':str(orgs[1]['id'])})[0] in (403,404)
 for i in range(2):
  for _ in range(30):
   code,raw,_=request('/v1/models',key=keys[i]);assert code==200
   visible=[r['id'] for r in json.loads(raw)['data']]
   if routes[i]['name'] in visible:break
   time.sleep(.5)
  assert routes[i]['name'] in visible and routes[1-i]['name'] not in visible
 out['checks']['model_directory_isolated']={'passed':True}
 # Real key/project mappings exercise task and artifact ownership end to end.
 bundle=root/'.tenant-bundle.json';fd=os.open(bundle,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:json.dump([{'id':'tenant-'+str(i),'org':orgs[i]['id'],'key':keys[i]} for i in range(2)],f)
 try:
  args=['docker','run','--rm','--network','host','-v','/root/wtaobj:/root/wtaobj','-w','/root/wtaobj/maas-framework','python:3.11-slim','/root/wtaobj/maas-framework/.venv/bin/python',str(root/'scripts/maas/p0/probe_task_tenants.py'),str(bundle)]
  completed=subprocess.run(args,capture_output=True,text=True)
  if completed.returncode:raise RuntimeError('Task tenant integration failed; inspect private runtime logs')
  out['task_api_isolation']=json.loads(completed.stdout)
 finally:bundle.unlink(missing_ok=True)
 admin('/v2/model-routes/'+str(routes[0]['id'])+'/access',{'access_policy':'allowed_principals','principals':[]})
 assert check('org_grant_revoked',keys[0],routes[0]['name'])[0] in (403,404)
 admin('/v2/model-routes/'+str(routes[0]['id'])+'/access',{'access_policy':'allowed_principals','principals':[{'principal_id':orgs[0]['id'],'principal_type':'org'}]})
 assert check('org_grant_restored',keys[0],routes[0]['name'])[0]==200
 admin('/v2/api-keys/'+str(infos[0]['id']),{'scope':['inference'],'allowed_model_names':['maas-not-allowed']},'PUT')
 assert check('model_whitelist_changed_denied',keys[0],routes[0]['name'])[0] in (403,404)
 info,expiring=res.key(ops[0],'short-expiry',[routes[0]['name']],orgs[0]['id'],expires=2)
 code,headers=check('before_expiry',expiring,routes[0]['name']);assert code==200
 ticket=headers.get('x-gpustack-auth-cache');time.sleep(3)
 assert check('expired_key_fresh_auth_denied',expiring,routes[0]['name'])[0] in (401,403)
 if ticket:
  # Demonstrates why the bridge never forwards client-provided cached authorization.
  code,_=check('expired_key_with_old_gateway_ticket',expiring,routes[0]['name'],{'x-gpustack-auth-cache':ticket})
  out['cached_ticket_reused_after_expiry']=(code==200)
 out['ok']=True
except Exception as exc:
 out['ok']=False;out['failure']={'type':type(exc).__name__,'message':str(exc)};raise
finally:
 out['cleanup_ok']=res.cleanup();out['cleanup_failures']=res.failures
 (root/'tenant-acceptance.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out))
