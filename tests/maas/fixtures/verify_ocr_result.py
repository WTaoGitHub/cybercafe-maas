"""Verify downloaded MaaS result and receipt JSON for the two fixed OCR fixtures."""
import argparse
import json
import re
from pathlib import Path


def words_in_order(text, words):
    tokens = re.findall(r"[a-z]+|[0-9]+", text.lower())
    offset = 0
    for word in words:
        try:
            offset = tokens.index(word, offset) + 1
        except ValueError:
            return False
    return True


def verify(model, result, receipt):
    outputs = result["outputs"]
    if not receipt.get("id") or result["metering"]["id"] != receipt["id"]:
        raise ValueError("Result and receipt IDs differ")
    if model == "document.unlimited-ocr-2":
        pages = outputs["pages"]
        if len(pages) != 2 or outputs["page_count"] != 2 or receipt.get("successful_pages") != 2:
            raise ValueError("Expected two output pages and two metered pages")
        for page, words in zip(pages, (("alpha", "2048"), ("bravo", "4096"))):
            if not words_in_order(page["markdown"], words):
                raise ValueError("Unexpected PDF page content or order")
    elif model in ("ocr.default", "ocr.ovisocr3"):
        words = ("quarterly", "service", "report", "qsr", "2026", "0410", "128", "12", "minutes", "complete")
        if receipt.get("successful_images") != 1 or not words_in_order(outputs["text"], words):
            raise ValueError("Unexpected image text, reading order or image count")
    else:
        raise ValueError("Unsupported fixture model")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=("ocr.default", "ocr.ovisocr3", "document.unlimited-ocr-2"))
    parser.add_argument("result", type=Path)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    verify(args.model, json.loads(args.result.read_bytes()), json.loads(args.receipt.read_bytes()))
    print("OCR result and metering receipt matched")
