#!/bin/sh
set -eu
cd /Users/4paradigm/dev/cybercafe-maas
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
exec /opt/homebrew/bin/npx "$@"
