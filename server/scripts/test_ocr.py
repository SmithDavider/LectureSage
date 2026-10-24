import asyncio
from pathlib import Path

from PIL import Image, ImageDraw

from app.services.ocr import recognize_image


async def main():
    image_path = Path("/tmp/keji_ocr_test.png")
    image = Image.new("RGB", (900, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 80), "12 / 45", fill="black")
    image.save(image_path)

    result = await recognize_image(image_path)
    print("OCR_PAGE", result["page"])
    print("OCR_TITLE", result["title"])
    print("OCR_LINES", result["lines"][:3])


if __name__ == "__main__":
    asyncio.run(main())

