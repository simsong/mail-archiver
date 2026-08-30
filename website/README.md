# Mail Archiver website

This is a small static GitHub Pages site. The source lives in the repository's
`website/` directory and is deployed by
`.github/workflows/pages.yml` with GitHub Pages Actions.

This directory-plus-Action model is preferred here over a long-lived
`gh-pages` branch: site changes are reviewed alongside the application, the
default branch remains the source of truth, and GitHub builds the published
artifact from a clean checkout. After merging, enable **Settings → Pages →
Build and deployment → Source: GitHub Actions**. The project site will then be
available at `https://simsong.github.io/mail-archiver/`.

The icon files under `assets/` are exact derivatives of
`gui/icons/rainbow-post.svg`. Regenerate the four PNG sizes with:

```console
for size in 48 64 128 192; do
  rsvg-convert -w "$size" -h "$size" -o "gui/icons/rainbow-post-$size.png" gui/icons/rainbow-post.svg
  rsvg-convert -w "$size" -h "$size" -o "website/assets/rainbow-post-$size.png" gui/icons/rainbow-post.svg
done
```
