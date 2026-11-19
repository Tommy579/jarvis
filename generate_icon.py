import shutil
import struct
import subprocess
import tempfile
from pathlib import Path


def _write_png_ico(svg_path: Path, ico_path: Path, sizes: tuple[int, ...] = (256, 128, 64, 32, 16)) -> None:
    """Genere un fichier ICO multi-tailles a partir de PNG convertis par rsvg-convert."""
    entries: list[tuple[int, bytes]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for sz in sizes:
            out_png = Path(tmp) / f"icon_{sz}.png"
            subprocess.run(
                ["rsvg-convert", "-w", str(sz), "-h", str(sz), str(svg_path), "-o", str(out_png)],
                check=True,
            )
            entries.append((sz, out_png.read_bytes()))

    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    dir_bytes = bytearray()
    data_bytes = bytearray()
    for sz, png_data in entries:
        dim = 0 if sz >= 256 else sz
        dir_bytes.extend(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png_data), offset))
        data_bytes.extend(png_data)
        offset += len(png_data)
    ico_path.write_bytes(header + bytes(dir_bytes) + bytes(data_bytes))


def create_ico() -> None:
    root = Path(__file__).resolve().parent
    svg_path = root / "web" / "public" / "favicon.svg"
    png_path = root / "jarvis_icon.png"
    ico_path = root / "jarvis_icon.ico"

    if svg_path.exists() and shutil.which("rsvg-convert"):
        subprocess.run(
            ["rsvg-convert", "-w", "256", "-h", "256", str(svg_path), "-o", str(png_path)],
            check=True,
        )
        try:
            from PIL import Image

            img = Image.open(png_path).convert("RGBA")
            img.save(ico_path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
        except ModuleNotFoundError:
            _write_png_ico(svg_path, ico_path)
    else:
        from PIL import Image, ImageDraw

        size = 256
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=64, fill="#0F141B")
        draw.arc([24, 24, 232, 232], start=25, end=315, fill="#38BDF8", width=18)
        draw.ellipse([101, 101, 155, 155], fill="#38BDF8")
        img.save(png_path, format="PNG")
        img.save(ico_path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])

    print(f"Icônes générées : {png_path.name}, {ico_path.name}")


if __name__ == "__main__":
    create_ico()
