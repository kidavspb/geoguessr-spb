#!/root/geoguessr-spb/venv/bin/python3
"""
favicon_gen.py — собрать набор фавиконок из одного SVG.

Отдаёшь SVG — рядом с ним создаётся папка `favicons/` (либо указываешь папку
вторым аргументом) с готовыми файлами:
  favicon.svg           копия исходника
  favicon-16.png        16x16
  favicon-32.png        32x32
  favicon.ico           16/32/48 в одном файле
  apple-touch-icon.png  180x180
  icon-192.png          192x192 (PWA)
  icon-512.png          512x512 (PWA)

Рендерит как есть: что отдал, то и получил — никаких фонов, скруглений и пр.
Зависимости: cairosvg + Pillow (стоят в venv, на который указывает shebang).

Использование:
  ./tools/favicon_gen.py путь/к/icon.svg [папка-назначение]
"""
import io
import os
import shutil
import sys

try:
    import cairosvg
    from PIL import Image
except ImportError as e:
    sys.exit(f"favicon_gen: нужны cairosvg и Pillow (нет '{getattr(e, 'name', e)}')")


def render(svg_path, size):
    """SVG -> PNG нужного размера, как есть (vector рендерится чётко на любом размере)."""
    png = cairosvg.svg2png(url=svg_path, output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def main():
    if len(sys.argv) not in (2, 3) or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)

    svg = sys.argv[1]
    if not os.path.isfile(svg):
        sys.exit(f"favicon_gen: файл не найден: {svg}")

    out = (sys.argv[2] if len(sys.argv) == 3 else
           os.path.join(os.path.dirname(os.path.abspath(svg)), "favicons"))
    os.makedirs(out, exist_ok=True)

    shutil.copyfile(svg, os.path.join(out, "favicon.svg"))
    render(svg, 16).save(os.path.join(out, "favicon-16.png"))
    render(svg, 32).save(os.path.join(out, "favicon-32.png"))
    # Отдаём Pillow готовый рендер каждого слоя, чтобы тонкие линии не
    # пересчитывались из 48 px повторным растровым уменьшением.
    ico_images = {size: render(svg, size) for size in (16, 32, 48)}
    ico_images[48].save(
        os.path.join(out, "favicon.ico"),
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=[ico_images[16], ico_images[32]],
    )
    render(svg, 180).save(os.path.join(out, "apple-touch-icon.png"))
    render(svg, 192).save(os.path.join(out, "icon-192.png"))
    render(svg, 512).save(os.path.join(out, "icon-512.png"))

    print(f"Готово: {out}")
    for n in ("favicon.svg", "favicon-16.png", "favicon-32.png",
              "favicon.ico", "apple-touch-icon.png", "icon-192.png",
              "icon-512.png"):
        print(f"  {n:22} {os.path.getsize(os.path.join(out, n)):>7} b")


if __name__ == "__main__":
    main()
