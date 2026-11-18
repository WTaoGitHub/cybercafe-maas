"""Regenerate the synthetic deployment probe with the project's pinned Pillow."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    image = Image.new('RGB', (1280, 900), 'white')
    draw = ImageDraw.Draw(image)
    title = ImageFont.load_default(size=52)
    body = ImageFont.load_default(size=36)
    draw.text((65, 60), 'Quarterly Service Report', font=title, fill='black')
    rows = [
        ('Report ID', 'QSR-2026-0410'),
        ('Ticket count', '128'),
        ('Average resolution time', '12 minutes'),
        ('Status', 'Complete'),
    ]
    for index, (label, value) in enumerate(rows):
        y = 180 + index * 125
        draw.rectangle((60, y, 1220, y + 125), outline='black', width=3)
        draw.line((655, y, 655, y + 125), fill='black', width=3)
        draw.text((85, y + 40), label, font=body, fill='black')
        draw.text((685, y + 40), value, font=body, fill='black')
    draw.text((65, 780), 'Synthetic OCR deployment sample. No personal data.', font=body, fill='black')
    image.save(Path(__file__).with_name('fixtures') / 'service-report.png')


if __name__ == '__main__':
    main()
