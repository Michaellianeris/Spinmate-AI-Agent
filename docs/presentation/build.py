"""Builds web/spinmate-presentation.pdf from the slides in docs/presentation/slides (needs: pip install playwright).

Each slide is a 1920x1080 <section> with inline styles. The slides are rendered as images, so the PDF looks
the same in every viewer (LinkedIn, mail apps, phones).
"""
import asyncio
import io
import json
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / "web" / "spinmate-presentation.pdf"
SCALE = 1.5                      # 2880 x 1620 px per slide

FONTS = """
@font-face{font-family:'Inter';font-weight:100 900;src:url(fonts/inter-latin-wght-normal.woff2) format('woff2');
  unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD}
@font-face{font-family:'Inter';font-weight:100 900;src:url(fonts/inter-latin-ext-wght-normal.woff2) format('woff2');
  unicode-range:U+0100-02AF,U+0304,U+0308,U+0329,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF}
@font-face{font-family:'Inter';font-weight:100 900;src:url(fonts/inter-greek-wght-normal.woff2) format('woff2');
  unicode-range:U+0370-0377,U+037A-037F,U+0384-038A,U+038C,U+038E-03A1,U+03A3-03FF}
@font-face{font-family:'JetBrains Mono';font-weight:400;src:url(fonts/jetbrains-mono-latin-400-normal.woff2) format('woff2')}
@font-face{font-family:'JetBrains Mono';font-weight:600;src:url(fonts/jetbrains-mono-latin-600-normal.woff2) format('woff2')}
"""
BASE = "html,body{margin:0;background:#120D26}*{margin:0;box-sizing:border-box}" \
       "section{position:relative;width:1920px;height:1080px;overflow:hidden}aside{display:none}" \
       "ol,ul{padding-left:1.25em}li{margin-bottom:6px}table{border-collapse:collapse}"


async def main():
    order = json.loads((HERE / "deck.json").read_text())["order"]
    page_file = HERE / "_render.html"
    shots = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=SCALE)
        for sid in order:
            slide = (HERE / "slides" / f"{sid}.html").read_text()
            page_file.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{FONTS}{BASE}</style>"
                                 f"</head><body>{slide}</body></html>")
            await pg.goto(page_file.as_uri())
            await pg.evaluate("document.fonts.ready")
            await pg.wait_for_timeout(300)
            shots.append(await pg.locator("section").first.screenshot(type="jpeg", quality=90))
        await b.close()
    page_file.unlink(missing_ok=True)
    images = [Image.open(io.BytesIO(s)).convert("RGB") for s in shots]
    # 216 dpi gives 13.33 x 7.5 in pages, the usual 16:9 slide size
    images[0].save(OUT, "PDF", save_all=True, append_images=images[1:], resolution=216.0)
    print("wrote", OUT, f"({len(images)} slides)")

asyncio.run(main())
