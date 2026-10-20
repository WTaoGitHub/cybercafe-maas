"""Run inside GPUStack server. Export only routing policy; never kubeconfig/secrets."""
import asyncio,json
from kubernetes_asyncio import client,config

async def main():
 await config.load_kube_config(config_file='/var/lib/gpustack/higress/kubeconfig')
 async with client.ApiClient() as api:
  routes=await client.NetworkingV1Api(api).list_ingress_for_all_namespaces()
  output=[]
  keep={'higress.io/proxy-next-upstream','higress.io/proxy-next-upstream-tries','higress.io/rewrite-target','higress.io/destination'}
  for r in routes.items:
   output.append({'name':r.metadata.name,'namespace':r.metadata.namespace,'policies':{k:v for k,v in (r.metadata.annotations or {}).items() if k in keep},'paths':[p.path for rule in r.spec.rules or [] for p in rule.http.paths]})
  plugins=await client.CustomObjectsApi(api).list_cluster_custom_object('extensions.higress.io','v1alpha1','wasmplugins')
  print(json.dumps({'ingresses':output,'plugins':[{'name':x['metadata']['name'],'phase':x.get('spec',{}).get('phase'),'priority':x.get('spec',{}).get('priority')} for x in plugins['items']]},indent=2))
asyncio.run(main())
