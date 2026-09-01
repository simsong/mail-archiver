# Mail Archiver website

This is a Zola site using the local `envelope-rainbow` theme. The checked-in
site is built by `.github/workflows/pages.yml` and deployed with GitHub Pages
Actions from `main`; there is no `gh-pages` branch. After the first merge,
select **Settings → Pages → Build and deployment → Source: GitHub Actions**.

The workflow resolves exact `v1.2.3`-shaped stable tags and
`v1.2.3-beta1`-shaped beta tags into `data/releases.toml` during the build.
The committed data file is the no-release fallback used for local previews.

Zola `0.23.4` is pinned in the workflow. To preview locally, install that
version and run `zola serve --root website`.
