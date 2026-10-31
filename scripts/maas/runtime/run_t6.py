"""One-shot T6 preparation, confirmation and soak. Never retries or resumes."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.snapshot import read_private,save_private,replace_private
from maas_bench.t6_preparation import file_sha,install_stop_handlers
from scripts.maas.runtime import prepare_t6,confirmation_tuning,soak_tuning


def run(config):
    root=Path(config['output_directory']);root.mkdir(mode=0o700,exist_ok=False)
    status={'state':'PREPARING','automatic_resume':False,'production_promoted':False}
    journal=root/'pipeline-status-private.json';save_private(journal,status)
    try:
        pconfig={**config,'output_directory':str(root/'preparation')}
        save_private(root/'preparation-config-private.json',pconfig)
        prepared=prepare_t6.run(pconfig)
        if prepared['state']!='READY':
            status.update(state=prepared['state'],formal_confirmation_started=False,two_hour_soak_started=False)
            replace_private(journal,status);return status
        refresh=root/'preparation/refresh-private.json';reservation=root/'preparation/reservation-private.json'
        common={**config,'fixture_refresh_file':str(refresh),'fixture_refresh_sha256':file_sha(refresh),
                'preparation_file':str(reservation),'preparation_sha256':file_sha(reservation)}
        confirm={**common,'output_directory':str(root/'confirmation')}
        soak={**common,'confirmation_proof_file':str(root/'confirmation/status.json'),'output_directory':str(root/'soak')}
        save_private(root/'confirmation-config-private.json',confirm);save_private(root/'soak-config-private.json',soak)
        status.update(state='CONFIRMING',formal_confirmation_started=True);replace_private(journal,status)
        confirmation_tuning.run(confirm)
        status.update(state='SOAK',two_hour_soak_started=True);replace_private(journal,status)
        soak_tuning.run(soak)
        final=read_private(root/'soak/status.json')
        if final['state']!='COMPLETE':raise RuntimeError('Soak did not complete')
        status.update(state='COMPLETE',formal_segment_seconds=final['formal_segment_seconds'],
                      selected_parameters=final['selected_parameters']);replace_private(journal,status)
        return status
    except BaseException as exc:
        status.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__)
        replace_private(journal,status);raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:
        install_stop_handlers();print(json.dumps(run(read_private(a.config))),flush=True)
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
