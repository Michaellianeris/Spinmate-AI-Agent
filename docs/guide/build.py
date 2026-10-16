"""Builds web/spinmate-guide.pdf from docs/guide/guide.html (needs: pip install playwright && playwright install chromium)."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / "web" / "spinmate-guide.pdf"


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        await pg.goto((HERE / "guide.html").as_uri())
        await pg.wait_for_timeout(500)
        # page numbers come from @page in guide.html, so the cover has none
        await pg.pdf(path=str(OUT), format="A4", print_background=True, prefer_css_page_size=True)
        await b.close()
    print("wrote", OUT)

asyncio.run(main())
