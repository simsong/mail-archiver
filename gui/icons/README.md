# Mail Archiver icon

`rainbow-post.svg` is the source icon for the Python application and the
website. Regenerate the checked-in PNG sizes with:

```console
for size in 48 64 128 192; do
  rsvg-convert -w "$size" -h "$size" -o "gui/icons/rainbow-post-$size.png" gui/icons/rainbow-post.svg
  rsvg-convert -w "$size" -h "$size" -o "website/static/icons/rainbow-post-$size.png" gui/icons/rainbow-post.svg
done
```
