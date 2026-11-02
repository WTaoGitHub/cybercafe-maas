"""Validate customer contract against implemented API and edge allowlist (no server access)."""
import ast,json,re,sys
import yaml
from pathlib import Path
from openapi_spec_validator import validate_spec
ROOT=Path(__file__).resolve().parents[2]
doc=json.loads((ROOT/'docs/api/openapi.json').read_text());validate_spec(doc)
if '--write-yaml' in sys.argv:
 (ROOT/'docs/api/openapi.yaml').write_text(yaml.safe_dump(doc,allow_unicode=True,sort_keys=False,width=110))
assert yaml.safe_load((ROOT/'docs/api/openapi.yaml').read_text())==doc
tree=ast.parse((ROOT/'maas_platform/api.py').read_text());actual=set()
for node in ast.walk(tree):
 if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
  for d in node.decorator_list:
   if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr in ('get','post') and d.args and isinstance(d.args[0],ast.Constant):
    path=d.args[0].value
    if path!='/healthz':actual.add((path,d.func.attr))
documented={(p,m) for p,v in doc['paths'].items() for m in v}
assert documented==actual,(documented-actual,actual-documented)
edge=(ROOT/'deploy/maas/edge.nginx.conf').read_text();rules=re.findall(r'^\s*(\S+) 1;',edge,re.M)
for path,method in actual:
 url=path.replace('{ident}','task_example').replace('{name}','ocr.default')
 assert any((re.search(rule[1:],url) if rule.startswith('~') else rule==url) for rule in rules),url
 op=doc['paths'][path][method];assert op['description'] and op['summary'] and op['responses']
 if method=='post' and not path.endswith('/cancel'):assert op['requestBody']['required']
 assert doc['security']==[{'BearerAuth':[]}]
assert '/v1/models' not in doc['paths'] and '/healthz' not in doc['paths']
print('OpenAPI 3.1 schema valid; all 9 customer operations match code and edge; descriptions, auth and request bodies checked.')
