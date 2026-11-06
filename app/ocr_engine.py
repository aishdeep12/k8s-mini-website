"""The actual OCR. Swap this out for Mistral OCR, PyMuPDF, a VLM, etc."""
import io

import pytesseract
from PIL import Image


def extract_text(image_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(image_bytes))
    image = image.convert("L")          # grayscale helps Tesseract
    return pytesseract.image_to_string(image).strip()
