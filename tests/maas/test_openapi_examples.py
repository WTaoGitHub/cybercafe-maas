import copy,importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('schema_examples',Path(__file__).resolve().parents[2]/'scripts/api/schema_examples.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
inline=module.inline_local_schema_refs


def test_example_refs_inlined_without_changing_original_or_constraints():
    source={'$defs':{'X':{'type':'string','minLength':1}},'type':'object','properties':{'image':{'$ref':'#/$defs/X','description':'original annotation'}}}
    old=copy.deepcopy(source);value=inline(source)
    assert source==old and '$defs' not in value
    assert value['properties']['image']=={'allOf':[{'type':'string','minLength':1}],'description':'original annotation'}


def test_json_pointer_escaping_and_arrays():
    assert inline({'$defs':{'a/b':{'type':'integer'}},'anyOf':[{'$ref':'#/$defs/a~1b'}]})=={'anyOf':[{'type':'integer'}]}


@pytest.mark.parametrize('schema',[{'$ref':'https://example.org/schema'}, {'$defs':{'X':{'$ref':'#/$defs/X'}},'$ref':'#/$defs/X'}])
def test_unsupported_external_or_recursive_examples_fail_explicitly(schema):
    with pytest.raises(ValueError):inline(schema)


def test_allof_siblings_do_not_overwrite_reference_constraints():
    schema={'$defs':{'X':{'type':'string'}},'$ref':'#/$defs/X','allOf':[{'minLength':2}]}
    assert inline(schema)=={'allOf':[{'type':'string'},{'allOf':[{'minLength':2}]}]}
