#!/usr/bin/env python3
"""HTTP example through an authorized SSH tunnel; Python 3, no dependencies.

Keep this command running in another Mac terminal:
  ssh -N -o ExitOnForwardFailure=yes -p 2023 -i ~/.ssh/id_ed25519 \
      -L 127.0.0.1:18831:127.0.0.1:18831 root@111.4.255.126

Permission check only:
  python3 examples/verify_customer_ocr_http.py
Permission check, then one metered OCR request:
  python3 examples/verify_customer_ocr_http.py --image /absolute/path/sample.png

Default key: ~/.maas-credentials/customer-a-key (mode 600).
No certificate is needed for loopback HTTP. Non-loopback HTTP is rejected.
Shares the single-request implementation in verify_customer_ocr.py;
keep both files in the same directory. No automatic retries or secret output.
"""

import sys
from verify_customer_ocr import main


if __name__ == '__main__':
    sys.exit(main(default_base_url='http://127.0.0.1:18831', allow_local_http=True,
                  description=__doc__))
