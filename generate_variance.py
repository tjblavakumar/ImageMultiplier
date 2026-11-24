#!/usr/bin/env python3
"""Generate printable image variations using the 1min.AI image editor."""

import argparse
import io
import json
import os
import secrets
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

import requests
from PIL import Image, ImageColor, ImageOps, UnidentifiedImageError


API_URL = "https://api.1min.ai/api"
MODEL = "black-forest-labs/flux-2-klein-4b"
DPI = 300
VARIATIONS = (
    "Use a different harmonious color palette.",
    "Change the colors and introduce a distinctive decorative pattern.",
    "Create a variation with different colors and textures.",
    "Change the color scheme and, where appropriate, adjust the line thickness.",
    "Add a different pattern and vary the color palette.",
)


class GenerationError(Exception):
    pass


def parse_size(value):
    parts = value.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT in inches, e.g. 2x2")
    try:
        dimensions = tuple(Decimal(part) for part in parts)
        if not all(d.is_finite() and d > 0 for d in dimensions):
            raise ValueError
        pixels = tuple(int((d * DPI).to_integral_value(rounding=ROUND_HALF_UP)) for d in dimensions)
    except (InvalidOperation, ValueError, OverflowError):
        raise argparse.ArgumentTypeError("size must contain positive dimensions in inches") from None
    if any(p < 1 or p > 10000 for p in pixels):
        raise argparse.ArgumentTypeError("each output dimension must be between 1 and 10000 pixels at 300 DPI")
    return pixels


def positive_int(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("count must be a positive integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("count must be a positive integer")
    return number


def background_color(value):
    try:
        color = ImageColor.getrgb(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "background color must be a named color or a hex color such as '#ffcc00'"
        ) from None
    if len(color) != 3:
        raise argparse.ArgumentTypeError("background color must be opaque")
    return value.lower(), color


def api_post(endpoint, api_key, **kwargs):
    try:
        response = requests.post(
            f"{API_URL}/{endpoint}",
            headers={"API-KEY": api_key},
            timeout=(15, 300),
            **kwargs,
        )
    except requests.RequestException:
        raise GenerationError(
            f"Network error during {endpoint}. The request may have been processed; "
            "check your 1min.AI account before retrying."
        ) from None
    if not response.ok:
        raise GenerationError(f"1min.AI {endpoint} request failed (HTTP {response.status_code}).")
    try:
        return response.json()
    except requests.exceptions.JSONDecodeError:
        raise GenerationError(f"1min.AI {endpoint} returned invalid JSON.") from None


def upload_image(path, api_key):
    with path.open("rb") as image_file:
        response = api_post("assets", api_key, files={"asset": (path.name, image_file)})
    try:
        asset_path = response["asset"]["key"]
        if not isinstance(asset_path, str) or not asset_path:
            raise ValueError
        return asset_path
    except (KeyError, TypeError, ValueError):
        raise GenerationError("1min.AI upload response did not contain an asset key.") from None


def generate_image(asset_path, prompt, seed, api_key):
    response = api_post(
        "features",
        api_key,
        json={
            "type": "IMAGE_EDITOR",
            "model": MODEL,
            "promptObject": {
                "imageUrl": asset_path,
                "prompt": prompt,
                "output_format": "png",
                "megapixels": "1",
                "go_fast": True,
                "seed": seed,
            },
        },
    )
    try:
        record = response["aiRecord"]
        status = record["status"]
        if status != "SUCCESS":
            raise GenerationError(f"1min.AI image generation did not succeed (status: {status}).")
        url = record["temporaryUrl"]
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise GenerationError("1min.AI generation response did not contain a valid image URL.") from None
    try:
        download = requests.get(url, timeout=(15, 120))
    except requests.RequestException:
        raise GenerationError("Could not download the generated image; the generation may have been charged.") from None
    if not download.ok:
        raise GenerationError(
            f"Image download failed (HTTP {download.status_code}); the generation may have been charged."
        )
    return download.content


def save_result(image_data, output_dir, input_path, size, prompt, seed, background):
    try:
        with Image.open(io.BytesIO(image_data)) as image:
            image = ImageOps.exif_transpose(image)
            image = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
            image = image.convert("RGBA")
            canvas = Image.new("RGBA", image.size, background[1] + (255,))
            image = Image.alpha_composite(canvas, image).convert("RGB")
            output_dir.mkdir(parents=True, exist_ok=True)
            base = f"{input_path.stem}_{datetime.now():%H%M%S}_{uuid4().hex[:8]}"
            output_path = output_dir / f"{base}.png"
            image.save(output_path, format="PNG", dpi=(DPI, DPI))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise GenerationError(f"Could not process or save the generated image: {exc}") from exc

    metadata = {
        "source": str(input_path),
        "model": MODEL,
        "prompt": prompt,
        "seed": seed,
        "size_pixels": list(size),
        "dpi": DPI,
        "background_color": background[0],
    }
    metadata_path = output_path.with_suffix(".json")
    try:
        with metadata_path.open("x", encoding="utf-8") as file:
            json.dump(metadata, file, indent=2)
            file.write("\n")
    except OSError as exc:
        raise GenerationError(f"Image saved to {output_path}, but metadata could not be saved: {exc}") from exc
    return output_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_image", type=Path, help="reference image (JPEG, PNG, or WebP)")
    parser.add_argument("size", type=parse_size, help="output size in inches, e.g. 2x2")
    parser.add_argument(
        "background", nargs="?", type=background_color, default=background_color("white"),
        help="background color (default: white); quote hex colors",
    )
    parser.add_argument("count", type=positive_int, help="number of variations to generate")
    args = parser.parse_args(argv)

    api_key = os.environ.get("ONEMIN_API_KEY", "").strip()
    if not api_key:
        parser.exit(1, "Error: set the ONEMIN_API_KEY environment variable first.\n")

    try:
        with Image.open(args.input_image) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise GenerationError("The reference image must be JPEG, PNG, or WebP.")
            image.verify()
        if args.input_image.stat().st_size > 10 * 1024 * 1024:
            raise GenerationError("The reference image exceeds 10 MiB.")

        print("Uploading reference image to 1min.AI...", flush=True)
        asset_path = upload_image(args.input_image, api_key)
        output_dir = Path("output") / datetime.now().strftime("%Y%m%d")
        for index in range(args.count):
            seed = secrets.randbelow(2**31)
            prompt = (
                "Create a creative variation of this image while keeping its main subject "
                "and overall composition recognizable. "
                + VARIATIONS[index % len(VARIATIONS)]
                + f" Use a solid {args.background[0]} background. "
                "Use rich, vibrant, bright colors with strong contrast for the subject."
            )
            print(f"Generating image {index + 1} of {args.count}...", flush=True)
            data = generate_image(asset_path, prompt, seed, api_key)
            path = save_result(data, output_dir, args.input_image, args.size, prompt, seed, args.background)
            print(f"Saved {path}", flush=True)
    except (GenerationError, OSError, UnidentifiedImageError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
