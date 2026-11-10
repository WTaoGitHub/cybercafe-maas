"""Local clone bootstrap commands. Never display credential payloads or raw exceptions."""
import argparse,json,sys
from .client import BootstrapError,LocalSettings,LocalState,MAX_BYTES,prepare


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--config',default='/etc/maas/bootstrap.json')
    p.add_argument('action',choices=('prepare','apply','status','receive-grant'))
    p.add_argument('--trigger',choices=('boot','manual'),default='manual')
    a=p.parse_args(argv)
    try:
        settings=LocalSettings(a.config)
        if a.action=='status':result=LocalState(settings.data['state_dir']).status()
        elif a.action=='prepare':result=prepare(settings)
        elif a.action=='receive-grant':
            from .delivery import target
            def exchange(binding):
                print(json.dumps(binding),flush=True)
                raw=sys.stdin.buffer.readline(MAX_BYTES+1)
                if len(raw)>MAX_BYTES:raise BootstrapError('DELIVERY_REQUEST_INVALID')
                try:return json.loads(raw)
                except (ValueError,UnicodeError):raise BootstrapError('DELIVERY_REQUEST_INVALID') from None
            result=target(settings,exchange)
        else:
            from .worker import apply
            result=apply(settings,trigger=a.trigger)
        print(json.dumps(result));return 0
    except BootstrapError as e:print(json.dumps({'code':e.code}));return 1
    except Exception:print(json.dumps({'code':'BOOTSTRAP_INTERNAL_ERROR'}));return 1
if __name__=='__main__':raise SystemExit(main())
