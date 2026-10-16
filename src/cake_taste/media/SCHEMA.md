# Media profile section

`Profile.section("media")` requires `root`, `timezone`, and `title`; optional
`viewer_url` controls the gallery header. The public commands are:

- `taste media publish --source FILE|DIR [--source ...] [--title ...]
  [--description ...] [--date YYYY-MM-DD] [--revision ...]`
- `taste media audit`
- `taste media import --source OLD_STORE --target NEW_ABSENT_STORE`
- `taste media render`

PNG, JPEG, MP4, WebM, and binary P6 PPM sources are accepted. PPM is converted
to PNG. New capture IDs use source basename, source hash, and capture timestamp,
so moving the source root does not change them. The catalog and HTML are written
atomically under one publish lock. The catalog retains local capture time in the
configured timezone, revision, source hash, output hash, and original basename.

The one-time import copies historical `catalog.json` and `index.html` bytes
without republishing. It copies every referenced SHA-named blob, verifies all
catalog references, bytes, and hashes through readback, preserves file mtimes
with `copy2`, and atomically renames an absent staging directory into place. Existing IDs and timestamps are
left untouched. The old live gallery must remain served until the root lane
reviews a fresh manifest and switches the sole serving route.
