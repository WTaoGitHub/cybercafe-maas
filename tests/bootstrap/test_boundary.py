import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('e5_boundary',Path('scripts/maas/runtime/bootstrap_boundary.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_multiple_cidrs_are_limited_to_one_port_interface_and_own_chain():
    cfg={'port':18840,'interface':'enp3s0','allowed_cidrs':['127.0.0.0/8','10.11.31.0/24','10.12.0.0/22']}
    rows=m.render(cfg,4)
    assert rows==['-A MAAS-E5-CONFIG -i lo -j ACCEPT',
                  '-A MAAS-E5-CONFIG -i enp3s0 -s 10.11.31.0/24 -j ACCEPT',
                  '-A MAAS-E5-CONFIG -i enp3s0 -s 10.12.0.0/22 -j ACCEPT',
                  '-A MAAS-E5-CONFIG -j DROP']
    assert m.render(cfg,6)==['-A MAAS-E5-CONFIG -i lo -j ACCEPT','-A MAAS-E5-CONFIG -j DROP']
    cfg['allowed_cidrs']=['0.0.0.0/0']
    with pytest.raises(ValueError):m.render(cfg,4)
