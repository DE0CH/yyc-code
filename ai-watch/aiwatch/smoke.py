"""CI smoke test of the image: the browser starts headed (under the Xvfb the caller starts) and renders a page."""
import asyncio
import os

from .browser import Browser


async def main():
    os.environ.setdefault("AIWATCH_NO_PROXY", "1")
    async with Browser() as b:
        p = await b.page()
        await p.set_content("<p>ok</p>")
        print("browser", await p.inner_text("p"))


if __name__ == "__main__":
    asyncio.run(main())
