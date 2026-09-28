"""App-Icon (Monitor mit drei Balken), nur Pillow nötig."""

from PIL import Image, ImageDraw

BG = "#0E1116"
TEXT = "#E8ECF2"
CPU = "#3FA9F5"
GPU = "#7BD85A"


def make_icon(size=64):
    """Kleiner Monitor mit drei Balken (wie das App-Icon)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.rounded_rectangle([2 * s, 6 * s, 62 * s, 48 * s], radius=6 * s, fill=TEXT)
    d.rectangle([7 * s, 11 * s, 57 * s, 43 * s], fill=BG)
    d.rectangle([26 * s, 48 * s, 38 * s, 55 * s], fill=TEXT)
    d.rectangle([16 * s, 55 * s, 48 * s, 60 * s], fill=TEXT)
    d.rectangle([13 * s, 28 * s, 22 * s, 40 * s], fill=CPU)
    d.rectangle([27 * s, 17 * s, 36 * s, 40 * s], fill=GPU)
    d.rectangle([41 * s, 23 * s, 50 * s, 40 * s], fill="#F5A623")
    return img
