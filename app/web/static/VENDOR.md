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

### The exact artifacts

Verified byte-for-byte against the tarball: each sha256 below is the file in
this repository and the file inside `@tabler/core@1.5.1` unpacked.

| File | Bytes | sha256 |
|---|---|---|
| `css/tabler.rtl.min.css` | 694,036 | `fd209c1b7cbe9cb8011d2d91faeb43d67e7c601fdebc7bc6863832694e52daed` |
| `js/tabler.min.js` | 85,439 | `d4c4c2768f166c308391e0cea44db593056f12b84a4eeef46d55e35ca46d6e60` |

**694 KB is the correct size of this artifact**, and it is the minified build:
seven lines, no whitespace, and the only comment is a 46-byte external
`sourceMappingURL` pointing at a `.map` file that is deliberately not
vendored. There is no inline base64 source map and nothing is concatenated.
Upstream's own unminified `tabler.rtl.css` is 801 KB, so the minifier is doing
its work.

Over the wire it is far smaller: **79.5 KB gzipped, 54.9 KB brotli**. Any
figure "under 70 KB" for Tabler is a compressed size, not a file size. The
other numbers that look like a plausible "small Tabler" are the optional
bundles this project does not vendor - `tabler-marketing.min.css` at 64.9 KB
and `tabler-payments.min.css` at 28.9 KB - which are marketing pages and
payment-provider logos, not the framework.

The font, `vazirmatn-variable.woff2`, is sha256 `4e3fa217d38fdafc1fea4414ceb58ca5e662cf0ab5fa735a8c8c20e8b42cad92`. woff2 is already
compressed; gzipping it makes it *larger*, so a server must not.

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
