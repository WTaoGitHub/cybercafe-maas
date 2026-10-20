"""Read-only guard for future worker fault tests. No worker/daemon is started here."""
import json,socket,subprocess


def require_distinct_engine(host_id,test_id):
    if not host_id or not test_id or host_id==test_id:
        raise RuntimeError('Distinct engine IDs are necessary but do not prove host isolation')


def require_dedicated_host(addresses,containers):
    if not addresses or {'10.11.31.1','10.11.31.2'} & set(addresses):
        raise RuntimeError('Worker fault injection requires a separate disposable VM')
    protected={'wta-gpustack-worker','wta-gpustack-server','cybercafe-ocr','cybercafe-h3'}
    if protected & set(containers):
        raise RuntimeError('Managed service containers found; fault injection refused')


def main():
    addresses=subprocess.check_output(['hostname','-I'],text=True).split()
    containers=subprocess.check_output(['docker','ps','-a','--format','{{.Names}}'],text=True).splitlines()
    require_dedicated_host(addresses,containers)
    print(json.dumps({'candidate_host':socket.gethostname(),'requires_operator_confirmation_of_disposable_vm':True}))

if __name__=='__main__':
    try:main()
    except RuntimeError as exc:raise SystemExit(str(exc))
