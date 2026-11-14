"""Draw Deck's mark (deck/static/icon.svg: "Stack", a deck of cards) as desktop/winres/deck.ico, every size Windows
asks for, with Pillow, from the SVG's own numbers. After changing the mark, run from the repo root:

    uv run --no-project --with pillow python desktop/winres/icon.py
    go run github.com/tc-hib/go-winres@v0.3.3 simply --icon desktop/winres/deck.ico --manifest gui \
        --product-name Deck --file-description Deck --out desktop/rsrc --arch amd64

The second line writes desktop/rsrc_windows_amd64.syso, which `go build` links into deck.exe and deck-app.exe (the
window's and Start menu entry's icon)."""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
S = 1024  # drawn large, then shrunk: smooth edges at every size


def mark() -> Image.Image:
    """Stack: three rounded cards, the front one Deck's orange with its LED (deck/static/icon.svg, 128-unit grid)."""
    k = S / 128
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=28 * k, fill="#141414")
    for y, h, fill in ((22, 54, "#5a5751"), (40, 54, "#b3afa6"), (58, 48, "#ff4f12")):
        d.rounded_rectangle([24 * k, y * k, 104 * k, (y + h) * k], radius=12 * k, fill=fill)
    d.ellipse([(40 - 6) * k, (74 - 6) * k, (40 + 6) * k, (74 + 6) * k], fill="#141414")
    return img


if __name__ == "__main__":
    big = mark().resize((256, 256), Image.LANCZOS)
    big.save(HERE / "deck.ico", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
    print(f"wrote {HERE / 'deck.ico'}")
