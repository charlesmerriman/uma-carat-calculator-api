# Fonts for the link-preview pictures

Drawn by `calculatorapi/share_card.py` into the pictures Discord and X show for a
gallery post (`/post/<id>/preview.jpg`) and a shared plan (`/plan/<id>/preview.jpg`).
Server-side only: nothing here is served to a browser.

| File | What | Licence |
|---|---|---|
| `Outfit-latin-wght.woff2` | Outfit, the site's wordmark face, variable weight, Latin subset. Copied from the frontend's `@fontsource-variable/outfit` (`files/outfit-latin-wght-normal.woff2`). | SIL OFL 1.1, `Outfit-OFL.txt` |
| `DejaVuSans-Bold-symbols.ttf` | DejaVu Sans Bold cut down to the characters Outfit lacks that catalogue names use (★ in the ★3 step-ups, ♡ in some support banners), plus arrows, shapes and extended Latin. | Bitstream Vera licence and public domain, `DejaVu-LICENSE.txt` |

`share_card.py` decides which file draws a character from Outfit's published Latin
ranges (`OUTFIT_RANGES`, from the package's `unicode.json`), so a name never shows a
blank box.

To cut the DejaVu file again (fontTools is not a project dependency; use a throwaway
virtualenv):

```bash
pip install fonttools
pyftsubset /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf \
  --unicodes="U+0100-024F,U+2190-21FF,U+25A0-27BF,U+2B50-2B55" \
  --output-file=DejaVuSans-Bold-symbols.ttf --no-hinting --desubroutinize
```
