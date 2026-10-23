"""Local clone bootstrap commands. Never display credential payloads or raw exceptions."""
import argparse,json,os
from .client import BootstrapError,LocalSettings,LocalState,prepare


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--config',default='/etc/maas/bootstrap.json')
    p.add_argument('action',choices=('prepare','apply','status'))
    p.add_argument('--trigger',choices=('boot','manual'),default='manual')
    a=p.parse_args(argv)
    try:
        settings=LocalSettings(a.config)
        if a.action=='status':result=LocalState(settings.data['state_dir']).status()
        elif a.action=='prepare':result=prepare(settings)
        else:
            from .worker import apply
            result=apply(settings,trigger=a.trigger)
        print(json.dumps(result));return 0
    except BootstrapError as e:print(json.dumps({'code':e.code}));return 1
    except Exception:print(json.dumps({'code':'BOOTSTRAP_INTERNAL_ERROR'}));return 1
if __name__=='__main__':raise SystemExit(main())
