# Vendored front-end assets

Everything the panel loads is served from this repository. There are no CDN
links, no `package.json`, no npm, no node and no build step: the files below
were downloaded once, at the exact versions pinned here, and committed.

To check that nothing reaches the network at runtime, the vendored CSS
contains no `@import` and no `url()` at all, and the only `http` strings in
either file are an SVG namespace and the licence URLs in their banners.

## Tabler 1.5.1

| | |
|---|---|
| Upstream | https://tabler.io - https://github.com/tabler/tabler |
| Version | `@tabler/core@1.5.1` (exact, pinned) |
| Source | the npm tarball `https://registry.npmjs.org/@tabler/core/-/core-1.5.1.tgz` |
| Licence | MIT, `vendor/tabler/LICENSE`, taken from the repository at tag `@tabler/core@1.5.1` |

Files taken from `dist/` only, never the SCSS source:

- `vendor/tabler/css/tabler.rtl.min.css` - the right-to-left build, which is
  the one this panel needs; the LTR build is not vendored.
- `vendor/tabler/js/tabler.min.js` - includes Bootstrap's JavaScript and
  Popper, so no separate Bootstrap bundle is needed.

Deliberately **not** vendored: the icon font (icons arrive later as inline
SVG), and every third-party plugin under `dist/libs` (charts, date pickers,
rich selects and the rest).

### Updating

1. Download the tarball for the new exact version and unpack it somewhere
   outside this repository.
2. Copy `dist/css/tabler.rtl.min.css` and `dist/js/tabler.min.js` over the
   files here. Copy nothing else.
3. Refresh `vendor/tabler/LICENSE` from the repository at the matching tag.
4. Update the version in this file.
5. Run the full test suite and the browser tests, then look at the shell
   preview, because a major version of Tabler can move class names.

## Vazirmatn 33.003

| | |
|---|---|
| Upstream | https://github.com/rastikerdar/vazirmatn |
| Version | `v33.003` (exact, pinned) |
| Source | the release archive `vazirmatn-v33.003.zip` |
| Licence | SIL Open Font License 1.1, `vendor/vazirmatn/LICENSE` (the archive's `OFL.txt`) |

- `vendor/vazirmatn/vazirmatn-variable.woff2` - the variable font from
  `fonts/webfonts/Vazirmatn[wght].woff2`, renamed only because square
  brackets are not valid in a URL unescaped. One file carries every weight
  from 100 to 900, which is smaller than shipping three static cuts.

### Updating

Download the new release archive, take `fonts/webfonts/Vazirmatn[wght].woff2`
and `OFL.txt`, rename them as above, and update the version here.
