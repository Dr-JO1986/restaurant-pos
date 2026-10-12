# Render file storage

Uploaded menu photos and online payment slips must be stored on persistent
storage. Render's web-service filesystem is otherwise replaced during deploys.

For the current web service:

1. In Render, open the web service and add a Persistent Disk. Use `/var/data`
   as its mount path. Persistent disks require a paid service plan.
2. Redeploy the service after the disk is attached.
3. In the service's Environment settings, set `MEDIA_ROOT` to
   `/var/data/media`, then save and redeploy if Render requests it.

When Render's `RENDER=true` environment variable is present, the application
defaults `MEDIA_ROOT` to `/var/data/media`; the explicit environment setting
above keeps the storage location clear and configurable.

Files uploaded before the persistent disk was attached are not copied
automatically. Restore any missing uploads from a backup or upload them again
after the disk is mounted. Files that are already in the repository's bundled
`media` directory can still be served as fallback menu images.
