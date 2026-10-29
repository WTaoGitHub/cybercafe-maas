import importlib.util,struct,sys
from pathlib import Path
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'scripts/maas/runtime'))
spec=importlib.util.spec_from_file_location('gateway_recreate',root/'scripts/maas/runtime/debug_gateway_recreate.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def frame(flags=2,port=10150):
    eth=b'\0'*12+b'\x08\x00'
    ip=bytearray(20);ip[0]=0x45;ip[9]=6;ip[12:16]=bytes([10,11,31,1]);ip[16:20]=bytes([10,11,31,3])
    tcp=bytearray(20);tcp[:4]=struct.pack('!HH',43210,port);tcp[13]=flags
    return eth+ip+tcp+b'Authorization: Bearer secret-not-to-log'


def test_only_connection_header_fields_are_persisted():
    v=m.tcp_metadata(frame(),1.0)
    assert v['syn'] and v['dst']=='10.11.31.3' and v['dport']==10150
    assert 'secret' not in str(v) and 'payload' not in v


def test_non_connection_packets_and_other_ports_are_ignored():
    assert m.tcp_metadata(frame(flags=16),1) is None
    assert m.tcp_metadata(frame(port=22),1) is None
    assert m.tcp_metadata(b'bad',1) is None
