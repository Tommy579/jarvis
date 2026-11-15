import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw


def create_ico():
    root = Path(__file__).resolve().parent
    svg_path = root / "web" / "public" / "favicon.svg"
    png_path = root / "omi_icon.png"
    ico_path = root / "omi_icon.ico"

    if svg_path.exists() and shutil.which("rsvg-convert"):
        subprocess.run(
            ["rsvg-convert", "-w", "256", "-h", "256", str(svg_path), "-o", str(png_path)],
            check=True,
        )
        img = Image.open(png_path).convert("RGBA")
    else:
        size = 256
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=60, fill="#0F141B")
        draw.arc([36, 36, 220, 220], start=25, end=315, fill="#38BDF8", width=16)
        draw.arc([72, 72, 184, 184], start=210, end=45, fill="#67E8F9", width=10)
        draw.ellipse([104, 104, 152, 152], fill="#38BDF8")
        img.save(png_path, format="PNG")

    img.save(ico_path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
    print(f"Icônes générées : {png_path.name}, {ico_path.name}")


if __name__ == "__main__":
    create_ico()
