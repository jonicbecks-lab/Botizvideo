# Vendored browser runtimes

These files are checked into the repository so production terminals do not execute code from a CDN.

| File | Upstream | Version | SHA-256 |
| --- | --- | --- | --- |
| `lightweight-charts.standalone.production.js` | `tradingview/lightweight-charts` | 5.2.0 | `c0992580867c4912cc9385b3c2728315bcc1a76c7f1087dca908430fccdf31d7` |
| `jszip-3.10.1.min.js` | `Stuk/jszip` / npm `jszip` | 3.10.1 | `acc7e41455a80765b5fd9c7ee1b8078a6d160bbbca455aeae854de65c947d59e` |

The JSZip npm tarball was verified against its registry integrity value:
`sha512-xXDvecyTpGLrqFrvkrUSoxxfJI5AH7U8zxxtVclpsUtMCq4JQ290LY8AW5c7Ggnr/Y/oK+bQMbqK2qmtk3pN4g==`.

Licenses are stored alongside the runtimes as `LICENSE.lightweight-charts` and `LICENSE.jszip`.
