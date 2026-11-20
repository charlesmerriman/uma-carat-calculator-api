# Images the API draws with

Pictures the server composes itself (link previews) read these with Pillow. They
are code assets, not media: nothing here goes to the Space.

## uma-frame.png

The site's silver uma frame, `frontend/src/assets/uma-frame.svg`, as a 762 x 762
transparent PNG (the SVG's own viewBox). `gallery_preview.py` draws it between
an uma's layered base and foreground, the order the site uses
(notes/layered-uma-art-plan.md). Pillow can't draw SVG and the API has no SVG
rasteriser, so the frame is rasterised once and checked in.

Remake it whenever the SVG changes, with headless Chrome on a transparent page,
then crop the top 762 x 762. The window must be TALLER than the frame: headless
Chrome's page is shorter than `--window-size`, and a 762-tall window cut the
frame's bottom band off (2026-11-20).

```bash
# frame.html: <img src="uma-frame.svg" style="display:block;width:762px;height:762px">
# with html, body { margin: 0; background: transparent }, beside a copy of the SVG
google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --default-background-color=00000000 --window-size=762,1100 \
  --screenshot=tall.png file:///abs/path/to/frame.html
python -c "from PIL import Image; Image.open('tall.png').crop((0, 0, 762, 762)).save('uma-frame.png', optimize=True)"
```

Check the result has all four sides: `Image.open('uma-frame.png').getbbox()` is
`(0, 0, 762, 762)`.

Then raise `LAYOUT` in `gallery_preview.py`, so crawlers fetch the new picture.
