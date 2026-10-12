# Cloudinary file storage on Render

Menu photos and online payment slips use Cloudinary when Cloudinary credentials
are present in the environment. Static assets continue to use WhiteNoise.

Configure either one of these options in the Render web service environment:

- `CLOUDINARY_URL` with the Cloudinary connection URL; or
- all three variables: `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, and
  `CLOUDINARY_API_SECRET`.

The app selects `cloudinary_storage.storage.MediaCloudinaryStorage` for the
Django default file storage when these credentials are configured. With no
Cloudinary credentials, local development continues to use `MEDIA_ROOT` (which
can be overridden with the `MEDIA_ROOT` environment variable).

Previously uploaded files are not copied to Cloudinary automatically. Upload
existing menu photos and payment slips to Cloudinary, or re-upload them from
the merchant dashboard. Bundled menu images under `media/menu_images` remain
available as a fallback.
