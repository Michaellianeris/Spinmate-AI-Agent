"""Builds web/spinmate-guide.pdf from docs/guide/guide.html (needs: pip install playwright && playwright install chromium)."""
import asyncio
import base64
from pathlib import Path
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / "web" / "spinmate-guide.pdf"


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        # the cover goes into the PDF as an image: some viewers (LinkedIn, Outlook) drop SVG gradients
        shot = await b.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=3)
        await shot.goto((HERE / "guide.html").as_uri())
        await shot.wait_for_timeout(500)
        jpg = await shot.locator(".cover").screenshot(type="jpeg", quality=92)
        await shot.close()

        pg = await b.new_page()
        await pg.goto((HERE / "guide.html").as_uri())
        await pg.wait_for_timeout(500)
        await pg.evaluate("""src => { const c = document.querySelector('.cover');
            c.innerHTML = `<img src="${src}" alt="SpinMate User Guide" style="position:absolute;inset:0;width:100%;height:100%">`;
            c.style.background = 'none'; }""", "data:image/jpeg;base64," + base64.b64encode(jpg).decode())
        await pg.wait_for_timeout(300)
        # page numbers come from @page in guide.html, so the cover has none
        await pg.pdf(path=str(OUT), format="A4", print_background=True, prefer_css_page_size=True)
        await b.close()
    print("wrote", OUT)

asyncio.run(main())
