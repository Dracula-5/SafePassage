#!/bin/sh
# One verified database backup a day, the newest KEEP kept. A dump only replaces the last good one
# after pg_restore has read it back, so a failed run leaves a backup you can use. Restore steps are
# in docs/security.md. Copy /backups off the VM too: a backup on the same disk dies with the disk.
set -u
KEEP="${KEEP:-7}"
EVERY_S="${EVERY_S:-86400}"

until pg_isready -q; do sleep 5; done
while true; do
    name="/backups/reliefroute-$(date -u +%Y%m%dT%H%M%SZ).dump"
    if pg_dump --format=custom --file="$name.partial" && pg_restore --list "$name.partial" > /dev/null; then
        mv "$name.partial" "$name"
        echo "backup ok: $name ($(du -h "$name" | cut -f1))"
        ls -1t /backups/*.dump | tail -n +"$((KEEP + 1))" | xargs -r rm -f
    else
        rm -f "$name.partial"
        echo "BACKUP FAILED at $(date -u)" >&2
    fi
    sleep "$EVERY_S"
done
