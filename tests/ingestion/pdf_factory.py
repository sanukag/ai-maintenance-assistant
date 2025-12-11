"""Small real-PDF fixture builder for integration tests."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def write_text_pdf(path: Path, text: str) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    content = DecodedStreamObject()
    escaped_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped_text}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as output:
        writer.write(output)


def write_scanned_image(path: Path, text: str, *, image_format: str = "PNG") -> None:
    """Write a high-contrast document scan suitable for real OCR tests."""

    image = Image.new("RGB", (1654, 2339), "white")
    drawing = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=54)
    drawing.multiline_text((130, 220), text, fill="black", font=font, spacing=28)
    image.save(path, format=image_format, dpi=(150, 150))
    image.close()


def write_scanned_pdf(path: Path, text: str) -> None:
    """Write a one-page image-only PDF with no embedded text layer."""

    image_path = path.with_suffix(".scan.png")
    write_scanned_image(image_path, text)
    with Image.open(image_path) as image:
        image.save(path, format="PDF", resolution=150)
    image_path.unlink()
