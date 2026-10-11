# Log rotation boundaries

The server handler owns its active `cao_<start>.log` stream and rotates it daily
or at the configured size cap. Complete gzip archives are published without
replacing an existing destination. A failed compression keeps the original
source. Retention considers only dated gzip archives for this exact server
filename, not arbitrary backups, symlinks, or files from other server starts.

Terminal logs are deliberately unchanged by this fix. An old mtime does not
prove a terminal or its writer has retired. The proposed age-only maintenance
loop could unlink an idle writer's file and make offset-range reads return
empty. That loop and its deletion timer have been removed before release.

Safe terminal archival remains follow-up work for aiva#512. It requires an
explicit writer-retirement boundary, draining pending output under lifecycle
synchronization, replay-safe ownership, and a defined archived range-read
contract. Unknown writers must remain untouched. Tests must cover idle open
writers, concurrent appends, path replacement, archive collisions, symlinks,
and offset reads before and after archival. No existing terminal logs or old
server generations are reclaimed by this PR.

These changes take effect on a later normal server restart. This PR does not
restart services or clean production files.
