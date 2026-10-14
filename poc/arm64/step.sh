#!/bin/bash
# Runs one PoC stage inside the container. Lines printed as "RESULT: ..." become a
# notice annotation; a failure becomes an error annotation with the end of the log,
# because Actions logs need a signed-in account but annotations are public.
title=$1
shift
log=".poc/$(printf '%s' "$title" | tr -c 'A-Za-z0-9.' '-').log"
escape() { sed -e 's/%/%25/g' -e 's/\r/%0D/g' | cut -c1-400 | awk 'BEGIN { ORS = "%0A" } { print }'; }
start=$(date +%s)
docker exec poc "$@" >"$log" 2>&1
status=$?
elapsed=$(( $(date +%s) - start ))
if [ "$status" -eq 0 ]; then
  echo "::notice title=PASS ${title} (${elapsed}s)::$(grep '^RESULT: ' "$log" | sed 's/^RESULT: //' | head -n 25 | escape)"
else
  echo "::error title=FAIL ${title} (${elapsed}s, exit ${status})::$( { grep '^RESULT: ' "$log" | sed 's/^RESULT: //'; echo '--- end of log ---'; tail -n 45 "$log"; } | escape)"
fi
cat "$log"
exit "$status"
