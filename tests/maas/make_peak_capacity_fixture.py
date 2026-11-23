"""Generate synthetic large OCR inputs for the three-model capacity trial."""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def ledger(width, height, rows, heading):
    image = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(image)
    body = ImageFont.load_default(size=max(24, width // 75))
    title = ImageFont.load_default(size=max(35, width // 50))
    draw.text((100, 70), heading, font=title, fill='black')
    spacing = (height - 290) // rows
    for index in range(rows):
        y = 180 + index * spacing
        draw.line((100, y + spacing - 7, width - 100, y + spacing - 7), fill=(175, 175, 175), width=2)
        text = (f'{index + 1:03}   Inventory reconciliation   |   Item R-{1531 + index * 19:05}   |   '
                f'Warehouse 04   |   {124.17 + index * 6.11:8.2f} USD')
        draw.text((120, y), text, font=body, fill='black')
    return image


def generate(output):
    output.mkdir(parents=True, exist_ok=False)
    ledger(3200, 4400, 62, 'Synthetic invoice reconciliation').save(output / 'pp.png')
    ledger(2400, 3200, 36, 'Synthetic inventory ledger').save(output / 'ovis.png')
    page = ledger(1240, 1754, 16, 'Synthetic receiving report')
    page.save(output / 'unlimited.pdf', save_all=True, append_images=[page] * 7, resolution=150)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new directory for the generated PNGs and PDF')
    generate(parser.parse_args().output)
