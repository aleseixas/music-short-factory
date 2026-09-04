from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont


def font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def centered(draw, y, text, fnt, fill):
    box = draw.textbbox((0, 0), text, font=fnt)
    w = box[2] - box[0]
    draw.text(((720 - w) / 2, y), text, font=fnt, fill=fill)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: create_test_end_card.py <slug>")
    root = Path(__file__).resolve().parents[1]
    out = root / "episodes" / sys.argv[1] / "assets" / "end_card_template.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)

    bg = (8, 8, 8)
    gold = (223, 178, 86)
    white = (246, 246, 246)
    muted = (170, 170, 170)
    img = Image.new("RGB", (720, 1280), bg)
    d = ImageDraw.Draw(img)

    d.ellipse((278, 82, 442, 246), outline=gold, width=5)
    d.arc((305, 110, 415, 220), 35, 325, fill=gold, width=6)
    d.line((330, 165, 390, 165), fill=gold, width=6)

    centered(d, 280, "ALÉM DO HIT", font(58, True), gold)
    centered(d, 355, "HISTÓRIAS POR TRÁS DAS MÚSICAS", font(22, False), muted)

    # Centro propositalmente limpo para o CTA dinâmico do text_fx.
    d.rounded_rectangle((120, 980, 600, 1060), radius=24, outline=gold, width=3)
    centered(d, 998, "CURTA   •   COMENTE   •   COMPARTILHE", font(22, True), white)
    centered(d, 1140, "PARA MAIS HISTÓRIAS POR TRÁS DOS HITS", font(19, False), muted)

    img.save(out, "JPEG", quality=92, optimize=True)
    print(f"Created valid test end card: {out}")


if __name__ == "__main__":
    main()
