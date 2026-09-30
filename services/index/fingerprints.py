from __future__ import annotations
import hashlib
import io


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dhash_bytes(data: bytes, size: int = 16) -> str | None:
    try:
        from PIL import Image, ImageOps
        image = Image.open(io.BytesIO(data)).convert('L')
        image = ImageOps.fit(image, (size + 1, size))
        pixels = list(image.getdata())
        bits = []
        for row in range(size):
            offset = row * (size + 1)
            bits.extend(pixels[offset + col] > pixels[offset + col + 1] for col in range(size))
        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)
        return f'{value:0{(size * size + 3) // 4}x}'
    except Exception:
        return None


def fingerprint(data: bytes) -> dict:
    result = {'sha256': sha256_bytes(data), 'phash': dhash_bytes(data)}
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        result.update({'width': image.width, 'height': image.height, 'mime_type': image.get_format()})
    except Exception:
        pass
    return result
