"""Validate customer contract against implemented API and edge allowlist (no server access)."""
import ast,json,re,sys
import yaml
from pathlib import Path
from openapi_spec_validator import validate_spec
from jsonschema import Draft202012Validator, FormatChecker
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
documented={(p,m) for p,v in doc['paths'].items() for m,op in v.items()
            if m in ('get','post') and op.get('x-implementation-status')!='draft'}
assert documented==actual,(documented-actual,actual-documented)
edge=(ROOT/'deploy/maas/edge.nginx.conf').read_text();rules=re.findall(r'^\s*(\S+) 1;',edge,re.M)
for path,method in actual:
 url=path.replace('{ident}','task_example').replace('{name}','ocr.default')
 assert any((re.search(rule[1:],url) if rule.startswith('~') else rule==url) for rule in rules),url
 op=doc['paths'][path][method];assert op['description'] and op['summary'] and op['responses']
 if method=='post' and not path.endswith('/cancel'):assert op['requestBody']['required']
 assert doc['security']==[{'BearerAuth':[]}]
assert '/v1/models' not in doc['paths'] and '/healthz' not in doc['paths']
count=0
for path_item in doc['paths'].values():
 for method,operation in path_item.items():
  if method not in ('get','post'):continue
  for response in operation['responses'].values():
   media=response.get('content',{}).get('application/json')
   if not media:continue
   schema={'allOf':[media['schema']],'components':doc['components']}
   values=[e['value'] for e in media.get('examples',{}).values()]
   if 'example' in media:values.append(media['example'])
   for value in values:
    Draft202012Validator(schema,format_checker=FormatChecker()).validate(value);count+=1
for path_item in doc['paths'].values():
 for method,operation in path_item.items():
  if method not in ('get','post'):continue
  for body in [operation.get('requestBody',{}),*operation['responses'].values()]:
   for media in body.get('content',{}).values():
    if body is operation.get('requestBody') and 'example' in media:
     Draft202012Validator({'allOf':[media['schema']],'components':doc['components']}).validate(media['example'])
     count+=1
    lines=[]
    if 'x-jsonl-line-schema' in media:
     lines.extend((media['x-jsonl-line-schema'],line) for line in media['example'].splitlines())
    for kind, schema in media.get('x-jsonl-line-schemas',{}).items():
     lines.extend((schema,line) for line in media['examples'][kind]['value'].splitlines())
    for schema,line in lines:
     Draft202012Validator({'allOf':[schema],'components':doc['components']}).validate(json.loads(line))
     count+=1
for name,schema in doc['components']['schemas'].items():
 for value in schema.get('examples',[]):
  Draft202012Validator({'$ref':'#/components/schemas/'+name,'components':doc['components']},format_checker=FormatChecker()).validate(value)
print(f'OpenAPI 3.1 schema valid; all {len(actual)} customer operations match code and edge; descriptions, auth and request bodies and {count} examples checked.')
