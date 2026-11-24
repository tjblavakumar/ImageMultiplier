# Image Variations

Create printable variations of a reference image with 1min.AI's Flux 2 Klein 4B image editor. The application runs locally, but uploads the reference image to 1min.AI for processing. Use only images you are comfortable sending to the service. An internet connection and 1min.AI API access and credits are required.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

In the terminal where you will run the application, enter your API key without displaying it:

```bash
read -rs -p "1min.AI API key: " ONEMIN_API_KEY; echo
export ONEMIN_API_KEY
```

Do not share the key or put it in source files.

## Usage

```bash
.venv/bin/python generate_variance.py mandala1.jpg 2x2 1
.venv/bin/python generate_variance.py mandala1.jpg 2x2 blue 1
.venv/bin/python generate_variance.py mandala1.jpg 2x2 '#ffcc00' 1
```

The arguments are the reference image, output width and height in inches, optional background color, and number of variations. The default background is white. Use a named color (such as `blue` or `lightblue`) or a quoted hex color (`'#ffcc00'`).

The model is prompted to use a solid background in the selected color and bright, vibrant, high-contrast subject colors. The API does not provide a background-color control, so the model may not follow these instructions exactly. Transparent areas in the returned image are filled with the selected background color; opaque backgrounds are not automatically replaced.

JPEG, PNG and WebP inputs up to 10 MiB are accepted. Each output is center-cropped to the requested aspect ratio and saved as a PNG at 300 DPI. For example, `2x2` produces 600 x 600 pixels.

Images and their generation settings are saved in `output/YYYYMMDD/` relative to the current directory. Existing files are not intentionally overwritten, and the original image is never modified. The model generates at approximately one megapixel; requesting a larger output can upscale it without adding detail.

Each variation requires an API generation request and may consume credits. Check model availability and pricing in your 1min.AI account before running. Start with one image to assess quality and cost. If a request times out, check your account before retrying: the service may have processed and charged for it. Earlier successful results remain saved if a later request fails.

Run local tests without calling the API:

```bash
.venv/bin/python -m unittest discover -s tests
```
