"""AI/ML pipeline — VLM image analysis for reference-driven 3D modeling.

Uses the HuggingFace Inference API (serverless) to run a Vision-Language
Model.  No local GPU required; inference runs on HF infrastructure.

The VLM analyses a product photo and returns a structured spec (shape,
parts, materials, dimensions, modeling hints) that the LLM uses to build
the object step-by-step with JustThreed's existing Blender tools.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import textwrap
from pathlib import Path
from typing import Any

# Default model for VLM analysis.  Override with env var.
_VLM_MODEL = os.environ.get(
    "JUSTTHREED_VLM_MODEL", "Qwen/Qwen2.5-VL-72B-Instruct"
)

_SPEC_PROMPT = textwrap.dedent("""\
    You are an expert 3D modeler.  Analyse this product image with extreme
    geometric precision.  A Blender artist will use your spec to recreate
    this object from primitives — every detail matters.

    Return a JSON object (no markdown fences, no commentary) with these keys:

    {
      "object_type": "short name, e.g. pump shampoo bottle",
      "category": "furniture | electronics | kitchenware | vehicle | clothing | architecture | cosmetics | organic | other",

      "overall_shape": {
        "cross_section": "describe the horizontal cross-section shape precisely: circular | elliptical | rectangular with rounded corners (specify corner radius as % of width) | square | hexagonal | other",
        "profile": "describe the vertical profile/silhouette: straight sides | tapered (specify direction) | curved (convex/concave) | hourglass | describe the exact curve",
        "symmetry": "bilateral | radial | asymmetric",
        "width_to_depth_ratio": "e.g. 1:1 for circular, 2:1 for flat rectangular",
        "height_to_width_ratio": "e.g. 3:1 for a tall narrow bottle"
      },

      "parts": [
        {
          "name": "e.g. bottle body",
          "primitive": "cylinder | cube | sphere | torus | cone | custom (describe)",
          "cross_section": "circular | rectangular_rounded | hexagonal | etc.",
          "relative_size": "percentage of total height or width, e.g. 70% of total height",
          "position": "bottom | middle | top | side",
          "details": "specific shape details: edge bevels, chamfers, tapers, curves. Be precise about angles and proportions."
        }
      ],

      "materials": [
        {
          "part": "which part this applies to",
          "surface": "glossy plastic | matte plastic | brushed metal | glass | ceramic | rubber | etc.",
          "color_hex": "#RRGGBB",
          "roughness": "0.0 (mirror) to 1.0 (fully rough), e.g. 0.15 for glossy plastic",
          "metallic": "0.0 (dielectric) to 1.0 (metal)"
        }
      ],

      "estimated_dimensions_cm": {
        "width": 7,
        "height": 20,
        "depth": 4
      },

      "modeling_steps": [
        "Numbered step-by-step instructions for building this in Blender from primitives. Be VERY specific about: which primitive to start with, exact cross-section shape, how to modify it (extrude, bevel, scale specific axes), how parts connect. Example: '1. Create a cube, scale X=3.5cm Z=15cm Y=2cm for the flat rectangular body. 2. Bevel all vertical edges with 0.5cm radius for rounded corners. 3. Select top face, inset 0.3cm, extrude up 1cm and scale inward for shoulder taper. 4. ...' Do NOT say just 'add a cylinder' — specify cross-section, dimensions, and modifications."
      ]
    }

    CRITICAL RULES:
    - Do NOT assume circular cross-sections.  Many bottles, containers, and
      products have FLAT / RECTANGULAR bodies with rounded corners.  Look
      carefully at the silhouette and perspective cues.
    - Specify proportions as ratios and percentages, not vague words.
    - Each modeling step must name the exact Blender operation and axis.
    - If the object has a pump, nozzle, cap, or mechanism — describe each
      sub-part with the same precision as the main body.
""")


def analyze_image(image_path: str) -> dict[str, Any]:
    """Send *image_path* to a VLM and return a structured product spec dict.

    Uses the HuggingFace Inference API with an OpenAI-compatible chat
    completions endpoint.  Set ``HF_TOKEN`` env var for authenticated
    (higher rate-limit) access, or leave unset for anonymous access.

    Falls back to a raw-text description if the VLM doesn't return valid JSON.
    """
    from huggingface_hub import InferenceClient

    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {path}")

    # Encode image as data URI.
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    data_uri = f"data:{mime};base64,{b64}"

    client = InferenceClient(token=os.environ.get("HF_TOKEN"))

    response = client.chat_completion(
        model=_VLM_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": _SPEC_PROMPT},
                ],
            }
        ],
        max_tokens=2048,
    )

    text = response.choices[0].message.content or ""

    # Try to extract JSON from the response (VLMs sometimes wrap it).
    json_match = re.search(r"\{[\s\S]*\}", text)
    if json_match:
        try:
            spec = json.loads(json_match.group())
            spec["_raw_response"] = text
            return spec
        except json.JSONDecodeError:
            pass

    # Couldn't parse structured JSON — return the raw text so the caller
    # (the LLM) can still work with it.
    return {
        "object_type": "unknown",
        "shape_description": text,
        "modeling_hints": [],
        "_raw_response": text,
        "_parse_error": "VLM response was not valid JSON; raw text returned.",
    }
