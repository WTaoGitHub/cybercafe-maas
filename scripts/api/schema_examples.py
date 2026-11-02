"""Inline embedded JSON Schema examples for OpenAPI preview compatibility.

JSON Schema references inside response *values* are not OpenAPI references,
but some preview bundlers resolve both against the OpenAPI document root.
This transformation is only for displayed examples, never server schemas.
"""


def inline_local_schema_refs(schema):
    def lookup(ref):
        if not isinstance(ref,str) or not ref.startswith('#/'):
            raise ValueError('Only local JSON Schema pointers can be inlined')
        value=schema
        for token in ref[2:].split('/'):
            value=value[token.replace('~1','/').replace('~0','~')]
        return value

    def expand(value,seen=()):
        if isinstance(value,list):return [expand(v,seen) for v in value]
        if not isinstance(value,dict):return value
        if '$ref' in value:
            ref=value['$ref']
            if ref in seen:raise ValueError('Recursive schema cannot be inlined for preview')
            target=expand(lookup(ref),seen+(ref,))
            siblings={k:expand(v,seen) for k,v in value.items() if k not in ('$ref','$defs')}
            if 'allOf' in siblings:return {'allOf':[target,siblings]}
            return {'allOf':[target],**siblings} if siblings else target
        return {k:expand(v,seen) for k,v in value.items() if k!='$defs'}
    return expand(schema)
