"""Pure in-memory checks of installed gpustack-runtime 0.2.1.

Run in a separate Python process inside the worker, with PYTHONDONTWRITEBYTECODE=1.
Uses dummy credentials and mocked Docker clients; no actual pull or deployment.
"""
import json
from types import SimpleNamespace
from unittest.mock import Mock,patch
from docker.errors import ImageNotFound
from gpustack_runtime.deployer.docker import DockerDeployer
from gpustack_runtime.deployer.__types__ import ContainerImagePullPolicyEnum as Policy,ContainerMount,ContainerMountModeEnum
from gpustack_runtime import envs
checks={}
def deployer(found):
 d=object.__new__(DockerDeployer)
 d._client=SimpleNamespace(images=SimpleNamespace(get=Mock(return_value=found)))
 d._pull_image=Mock(return_value='pulled')
 return d
d=deployer('cached');assert d._get_image('example.invalid/model:r1',Policy.IF_NOT_PRESENT)=='cached' and not d._pull_image.called
checks['if_not_present_cache_hit_no_pull']=True
d=deployer('cached');d._client.images.get.side_effect=ImageNotFound('mock missing')
assert d._get_image('example.invalid/model:r1',Policy.IF_NOT_PRESENT)=='pulled' and d._pull_image.call_count==1
checks['if_not_present_cache_miss_pulls']=True
d=deployer('cached');assert d._get_image('example.invalid/model:r1',Policy.ALWAYS)=='pulled' and not d._client.images.get.called
checks['always_pulls']=True
d=deployer('cached');d._client.images.get.side_effect=ImageNotFound('mock missing')
try:d._get_image('example.invalid/model:r1',Policy.NEVER)
except ImageNotFound:pass
else:raise AssertionError('Never policy accepted missing image')
assert not d._pull_image.called
checks['never_does_not_pull']=True
with patch.object(envs,'GPUSTACK_RUNTIME_DEPLOY_DEFAULT_CONTAINER_REGISTRY_USERNAME','dummy-reader'),patch.object(envs,'GPUSTACK_RUNTIME_DEPLOY_DEFAULT_CONTAINER_REGISTRY_PASSWORD','dummy-password'):
 assert DockerDeployer._get_registry_auth_from_envs()=={'username':'dummy-reader','password':'dummy-password'}
 checks['default_credentials_resolved']=True
 assert DockerDeployer._get_registry_auth_from_envs([SimpleNamespace(name='GPUSTACK_RUNTIME_DEPLOY_DEFAULT_CONTAINER_REGISTRY_USERNAME',value='model-reader'),SimpleNamespace(name='GPUSTACK_RUNTIME_DEPLOY_DEFAULT_CONTAINER_REGISTRY_PASSWORD',value='model-password')])=={'username':'model-reader','password':'model-password'}
 checks['workload_credentials_override_defaults']=True
options={}
m=ContainerMount(path='/srv/maas/model-research',mode=ContainerMountModeEnum.ROX)
DockerDeployer._append_container_mounts(options,SimpleNamespace(files=None,mounts=[m]),{})
assert options['mounts'][0]['Source']=='/srv/maas/model-research' and options['mounts'][0]['Target']=='/srv/maas/model-research' and options['mounts'][0]['ReadOnly']
checks['direct_mount_preserves_path_and_readonly']=True
print(json.dumps({'checks':checks,'count':len(checks),'scope':'pure in-memory mocks against installed native runtime; no Docker API or registry contacted','remote_configuration_changed':False}))
