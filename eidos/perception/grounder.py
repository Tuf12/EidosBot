"""Visual Grounding & Set-of-Mark (SoM) coordinate resolver."""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from eidos.models.router import ModelRouter
from eidos.perception.screen import ScreenCapture

logger = logging.getLogger(__name__)

# Colors for Set-of-Mark bounding boxes & badges
SOM_COLORS = [
    "#ff5555", "#50fa7b", "#8be9fd", "#bd93f9",
    "#ffb86c", "#ff79c6", "#f1fa8c", "#00f0ff"
]


class CoordinateMapper:
    """Utilities for converting between normalized and screen pixel coordinates."""

    @staticmethod
    def norm_to_screen(
        norm_x: float, norm_y: float, screen_w: int, screen_h: int, base: float = 1000.0
    ) -> Tuple[int, int]:
        """Convert normalized (0-1000) coordinates to screen pixels."""
        x = int(round((norm_x / base) * screen_w))
        y = int(round((norm_y / base) * screen_h))
        # Clamp within screen bounds
        x = max(0, min(screen_w - 1, x))
        y = max(0, min(screen_h - 1, y))
        return x, y

    @staticmethod
    def parse_coordinates(text: str, screen_w: int, screen_h: int) -> Optional[Tuple[int, int]]:
        """
        Parse predicted coordinates from model text output.
        Supports:
        - {"point": [x, y]} or {"point": [y, x]}
        - [x, y] or (x, y)
        - [ymin, xmin, ymax, xmax] (calculates center point)
        """
        if not text:
            return None

        # Pattern 1: {"point": [x, y]}
        point_match = re.search(r'["\']point["\']\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', text)
        if point_match:
            val1 = float(point_match.group(1))
            val2 = float(point_match.group(2))
            # Determine if normalized (0-1000) or raw pixels
            if val1 <= 1000 and val2 <= 1000 and (screen_w > 1000 or screen_h > 1000):
                return CoordinateMapper.norm_to_screen(val1, val2, screen_w, screen_h)
            return int(val1), int(val2)

        # Pattern 2: Bounding box [ymin, xmin, ymax, xmax] or [xmin, ymin, xmax, ymax]
        box_match = re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]', text)
        if box_match:
            c1, c2, c3, c4 = map(float, box_match.groups())
            # Usually [ymin, xmin, ymax, xmax] in Qwen-VL
            center_x = (c2 + c4) / 2.0
            center_y = (c1 + c3) / 2.0
            if center_x <= 1000 and center_y <= 1000 and (screen_w > 1000 or screen_h > 1000):
                return CoordinateMapper.norm_to_screen(center_x, center_y, screen_w, screen_h)
            return int(center_x), int(center_y)

        # Pattern 3: (x, y) or [x, y] coordinate pair
        pair_match = re.search(r'(?:point|at|coordinate|coords)?\s*[\(\[]\s*(\d+)\s*,\s*(\d+)\s*[\)\]]', text, re.IGNORECASE)
        if pair_match:
            x_val = float(pair_match.group(1))
            y_val = float(pair_match.group(2))
            if x_val <= 1000 and y_val <= 1000 and (screen_w > 1000 or screen_h > 1000):
                return CoordinateMapper.norm_to_screen(x_val, y_val, screen_w, screen_h)
            return int(x_val), int(y_val)

        return None


class SetOfMarkAnnotator:
    """Overlays visual badges and labeled bounding boxes onto screenshots."""

    @staticmethod
    def annotate_boxes(
        image: Image.Image,
        boxes: List[Tuple[int, int, int, int]]  # (x, y, w, h)
    ) -> Tuple[Image.Image, Dict[int, Tuple[int, int]]]:
        """
        Draw numbered bounding boxes onto a copy of the image.
        Returns: (annotated_image, {tag_number: (center_x, center_y)})
        """
        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        tag_map: Dict[int, Tuple[int, int]] = {}

        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        for idx, (x, y, w, h) in enumerate(boxes, start=1):
            color = SOM_COLORS[(idx - 1) % len(SOM_COLORS)]
            center_x = x + w // 2
            center_y = y + h // 2
            tag_map[idx] = (center_x, center_y)

            # Draw outer rectangle
            draw.rectangle([x, y, x + w, y + h], outline=color, width=2)

            # Draw badge background
            badge_text = str(idx)
            badge_w = len(badge_text) * 8 + 6
            badge_h = 14
            badge_x = x
            badge_y = max(0, y - badge_h)

            draw.rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], fill=color)
            draw.text((badge_x + 3, badge_y + 1), badge_text, fill="black", font=font)

        return annotated, tag_map

    @staticmethod
    def annotate_grid(image: Image.Image, rows: int = 10, cols: int = 10) -> Image.Image:
        """Draw an alphanumeric coordinate reference grid over the image."""
        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        w, h = annotated.size

        col_step = w / cols
        row_step = h / rows

        for c in range(1, cols):
            x = int(c * col_step)
            draw.line([(x, 0), (x, h)], fill=(255, 255, 255, 80), width=1)

        for r in range(1, rows):
            y = int(r * row_step)
            draw.line([(0, y), (w, y)], fill=(255, 255, 255, 80), width=1)

        return annotated


class VisualGrounder:
    """Resolves natural language UI target descriptions into screen coordinates."""

    def __init__(self, model_router: Optional[ModelRouter] = None):
        self.router = model_router or ModelRouter()

    def ground(
        self,
        target_description: str,
        image: Image.Image,
        active_window_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        Locate the target UI element on screen using multimodal vision.
        Returns exact (x, y) coordinates on screen.
        """
        w, h = image.size
        img_b64 = ScreenCapture.to_base64(image, format="JPEG", quality=85)

        system_prompt = (
            "You are a precise GUI visual grounding model. Your job is to locate user interface "
            "elements and return their exact click coordinates in the format: point: [x, y], "
            "where x and y are normalized to a 0-1000 scale."
        )

        user_prompt = (
            f"Locate the UI element: '{target_description}' on the screen. "
            f"Screen dimensions are {w}x{h}. "
            "Respond ONLY with the coordinate point: point: [x, y]"
        )

        # Call model router with dense_grounding enabled (escalates to Chutes Qwen3.5-397B if in AUTO mode)
        resp = self.router.vision_chat(
            prompt=user_prompt,
            image_base64=img_b64,
            system_prompt=system_prompt,
            dense_grounding=True,
        )

        coords = CoordinateMapper.parse_coordinates(resp.content, w, h)
        if coords:
            logger.info("Successfully grounded '%s' at %s (model: %s)", target_description, coords, resp.model)
            return coords

        logger.warning("Failed to parse coordinates for '%s'. Raw response: %s", target_description, resp.content)
        return None
