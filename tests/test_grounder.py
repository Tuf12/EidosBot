"""Tests for visual grounding, coordinate mapping, and Set-of-Mark annotations."""

import pytest
from PIL import Image

from eidos.perception.grounder import CoordinateMapper, SetOfMarkAnnotator, VisualGrounder


def test_coordinate_mapper_normalization():
    # 500, 500 on a 1920x1080 screen -> 960, 540
    sx, sy = CoordinateMapper.norm_to_screen(500, 500, 1920, 1080)
    assert sx == 960
    assert sy == 540

    # Boundary clamping
    sx, sy = CoordinateMapper.norm_to_screen(1200, -50, 1920, 1080)
    assert sx == 1919
    assert sy == 0


def test_coordinate_mapper_parsing():
    screen_w, screen_h = 1920, 1080

    # Pattern: {"point": [500, 500]}
    coords = CoordinateMapper.parse_coordinates('The button is at {"point": [500, 500]}', screen_w, screen_h)
    assert coords == (960, 540)

    # Pattern: [ymin, xmin, ymax, xmax] -> center calculation
    # ymin=200, xmin=400, ymax=400, xmax=600 -> center_x = 500, center_y = 300
    coords = CoordinateMapper.parse_coordinates('Target box: [200, 400, 400, 600]', screen_w, screen_h)
    assert coords == (960, 324)

    # Pattern: point (500, 500)
    coords = CoordinateMapper.parse_coordinates('Coordinates are (500, 500)', screen_w, screen_h)
    assert coords == (960, 540)

    # Invalid string
    assert CoordinateMapper.parse_coordinates('No coordinates here', screen_w, screen_h) is None


def test_set_of_mark_annotator():
    img = Image.new("RGB", (400, 300), color="white")
    boxes = [
        (20, 20, 80, 40),
        (150, 100, 100, 50)
    ]

    annotated, tag_map = SetOfMarkAnnotator.annotate_boxes(img, boxes)
    assert annotated.size == (400, 300)
    assert len(tag_map) == 2
    # Check centers
    assert tag_map[1] == (60, 40)
    assert tag_map[2] == (200, 125)


def test_grid_annotator():
    img = Image.new("RGB", (200, 200), color="black")
    grid_img = SetOfMarkAnnotator.annotate_grid(img, rows=4, cols=4)
    assert grid_img.size == (200, 200)
