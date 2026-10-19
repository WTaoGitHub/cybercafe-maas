#!/bin/sh
set -eu
cd /Users/4paradigm/dev/cybercafe/wtaobj
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
exec /opt/homebrew/bin/npx "$@"
