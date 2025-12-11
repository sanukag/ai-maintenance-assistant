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


def write_scanned_diagram_pdf(path: Path) -> None:
    """Write an image-only PDF containing a labelled pump-flow diagram."""

    image = Image.new("RGB", (1654, 2339), "white")
    drawing = ImageDraw.Draw(image)
    heading_font = ImageFont.load_default(size=58)
    label_font = ImageFont.load_default(size=48)
    drawing.text((130, 160), "SCANNED PUMP FLOW DIAGRAM", fill="black", font=heading_font)
    drawing.rectangle((170, 650, 570, 950), outline="black", width=10)
    drawing.text((315, 755), "P1", fill="black", font=label_font)
    drawing.line((570, 800, 960, 800), fill="black", width=12)
    drawing.polygon(
        [(960, 800), (900, 755), (900, 845)],
        fill="black",
    )
    drawing.rectangle((960, 650, 1360, 950), outline="black", width=10)
    drawing.text((1105, 755), "V1", fill="black", font=label_font)
    drawing.text(
        (170, 1040),
        "Flow from pump P1 to isolation valve V1",
        fill="black",
        font=label_font,
    )
    image.save(path, format="PDF", resolution=150)
    image.close()


def write_diagram_pdf(path: Path) -> None:
    """Write a digital-text PDF containing a simple vector pump-flow diagram."""

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
    content.set_data(
        b"BT /F1 18 Tf 72 720 Td (Pump flow schematic) Tj ET\n"
        b"2 w 90 520 100 70 re S\n"
        b"BT /F1 14 Tf 115 552 Td (P1) Tj ET\n"
        b"190 555 m 310 555 l S\n"
        b"300 565 m 310 555 l 300 545 l S\n"
        b"310 520 100 70 re S\n"
        b"BT /F1 14 Tf 342 552 Td (V1) Tj ET\n"
        b"410 555 m 520 555 l S\n"
        b"BT /F1 12 Tf 90 470 Td (Flow: P1 to isolation valve V1) Tj ET\n"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as output:
        writer.write(output)
