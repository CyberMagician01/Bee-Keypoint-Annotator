"""与界面无关的几何算法。"""

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]


def direction_angle_degrees(
    first_head: Sequence[float],
    first_tail: Sequence[float],
    second_head: Sequence[float],
    second_tail: Sequence[float],
) -> Optional[float]:
    """计算两条“头指向尾”方向向量之间的夹角，范围为 0～180°。"""
    first_x = float(first_tail[0]) - float(first_head[0])
    first_y = float(first_tail[1]) - float(first_head[1])
    second_x = float(second_tail[0]) - float(second_head[0])
    second_y = float(second_tail[1]) - float(second_head[1])
    first_length = math.hypot(first_x, first_y)
    second_length = math.hypot(second_x, second_y)
    if first_length <= 1e-9 or second_length <= 1e-9:
        return None
    cosine = (first_x * second_x + first_y * second_y) / (
        first_length * second_length
    )
    return math.degrees(math.acos(min(1.0, max(-1.0, cosine))))


def rect_bounds(points: Sequence[Sequence[float]]) -> Rect:
    """把 X-AnyLabeling 矩形的 2/4 个点统一转换为 x1,y1,x2,y2。"""
    if not points:
        raise ValueError("矩形点列表不能为空")
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def rect_area(rect: Rect) -> float:
    x1, y1, x2, y2 = rect
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def rect_center(rect: Rect) -> Point:
    x1, y1, x2, y2 = rect
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def point_in_rect(point: Point, rect: Rect, epsilon: float = 1e-6) -> bool:
    x, y = point
    x1, y1, x2, y2 = rect
    return (
        x1 - epsilon <= x <= x2 + epsilon
        and y1 - epsilon <= y <= y2 + epsilon
    )


def rectangle_drag_mode(
    point: Point,
    rect: Rect,
    tolerance: float,
) -> str:
    """判断 Ctrl+左键命中的矩形区域：框内移动，边缘/四角缩放。"""
    x, y = point
    x1, y1, x2, y2 = rect
    if not (
        x1 - tolerance <= x <= x2 + tolerance
        and y1 - tolerance <= y <= y2 + tolerance
    ):
        return ""

    near_left = abs(x - x1) <= tolerance
    near_right = abs(x - x2) <= tolerance
    near_top = abs(y - y1) <= tolerance
    near_bottom = abs(y - y2) <= tolerance
    horizontal = "w" if near_left else ("e" if near_right else "")
    vertical = "n" if near_top else ("s" if near_bottom else "")
    if horizontal or vertical:
        return vertical + horizontal
    return "move" if point_in_rect(point, rect) else ""


def rectangle_handle_mode(
    point: Point,
    rect: Rect,
    tolerance: float,
) -> str:
    """只命中矩形的八个控制点，并返回对应的缩放方向。"""
    x1, y1, x2, y2 = rect
    middle_x = (x1 + x2) / 2.0
    middle_y = (y1 + y2) / 2.0
    handles = (
        ("nw", (x1, y1)),
        ("n", (middle_x, y1)),
        ("ne", (x2, y1)),
        ("e", (x2, middle_y)),
        ("se", (x2, y2)),
        ("s", (middle_x, y2)),
        ("sw", (x1, y2)),
        ("w", (x1, middle_y)),
    )
    maximum_distance_squared = max(0.0, tolerance) ** 2
    matches = [
        ((point[0] - handle[0]) ** 2 + (point[1] - handle[1]) ** 2, mode)
        for mode, handle in handles
        if (point[0] - handle[0]) ** 2 + (point[1] - handle[1]) ** 2
        <= maximum_distance_squared
    ]
    return min(matches)[1] if matches else ""


def adjust_rectangle_bounds(
    rect: Rect,
    start: Point,
    current: Point,
    mode: str,
    image_size: Tuple[float, float],
    minimum_size: float = 4.0,
) -> Rect:
    """根据拖动方式计算新矩形，并限制在图片范围与最小尺寸内。"""
    x1, y1, x2, y2 = rect
    image_width, image_height = image_size
    image_width = max(0.0, float(image_width))
    image_height = max(0.0, float(image_height))
    minimum_width = min(max(0.0, minimum_size), image_width)
    minimum_height = min(max(0.0, minimum_size), image_height)
    dx = float(current[0]) - float(start[0])
    dy = float(current[1]) - float(start[1])

    if mode == "move":
        width = min(x2 - x1, image_width)
        height = min(y2 - y1, image_height)
        new_x1 = min(max(x1 + dx, 0.0), max(0.0, image_width - width))
        new_y1 = min(max(y1 + dy, 0.0), max(0.0, image_height - height))
        return new_x1, new_y1, new_x1 + width, new_y1 + height

    new_x1, new_y1, new_x2, new_y2 = x1, y1, x2, y2
    if mode in {"nw", "w", "sw"}:
        new_x1 = min(max(x1 + dx, 0.0), x2 - minimum_width)
    if mode in {"ne", "e", "se"}:
        new_x2 = max(min(x2 + dx, image_width), x1 + minimum_width)
    if mode in {"nw", "n", "ne"}:
        new_y1 = min(max(y1 + dy, 0.0), y2 - minimum_height)
    if mode in {"sw", "s", "se"}:
        new_y2 = max(min(y2 + dy, image_height), y1 + minimum_height)
    return new_x1, new_y1, new_x2, new_y2


def iou(rect_a: Rect, rect_b: Rect) -> float:
    """计算两个轴对齐矩形框的交并比。"""
    ax1, ay1, ax2, ay2 = rect_a
    bx1, by1, bx2, by2 = rect_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = rect_area(rect_a) + rect_area(rect_b) - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def symmetric_point(
    point: Point,
    rect: Rect,
    ratio: float = 1.0,
    clamp_to_rect: bool = False,
) -> Point:
    """
    以矩形中心为基准，按比例求 point 的近似中心对称点。

    ratio=1.0 是严格中心对称；小于 1 时靠近中心，大于 1 时远离中心。
    """
    if ratio < 0.0:
        raise ValueError("中心对称比例不能小于 0")
    cx, cy = rect_center(rect)
    target_x = cx + ratio * (cx - point[0])
    target_y = cy + ratio * (cy - point[1])
    if clamp_to_rect:
        x1, y1, x2, y2 = rect
        target_x = min(max(target_x, x1), x2)
        target_y = min(max(target_y, y1), y2)
    return target_x, target_y


def relative_position(point: Point, rect: Rect) -> Point:
    """计算关键点在框内的归一化位置 (u, v)。"""
    x1, y1, x2, y2 = rect
    width = x2 - x1
    height = y2 - y1
    if width <= 0.0 or height <= 0.0:
        raise ValueError("矩形宽高必须大于 0")
    return (point[0] - x1) / width, (point[1] - y1) / height


def point_from_relative(relative: Point, rect: Rect) -> Point:
    """把框内归一化位置映射到目标矩形框。"""
    u, v = relative
    x1, y1, x2, y2 = rect
    return x1 + u * (x2 - x1), y1 + v * (y2 - y1)


def greedy_iou_match(
    source_rectangles: Sequence[Dict],
    target_rectangles: Sequence[Dict],
    threshold: float,
) -> List[Tuple[int, int, float]]:
    """
    按 IoU 从高到低进行一对一匹配。

    输入项格式：
        {"rect": (x1, y1, x2, y2), "label": "bee"}
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("IoU 阈值必须在 0 到 1 之间")

    candidates: List[Tuple[float, int, int]] = []
    for source_index, source in enumerate(source_rectangles):
        for target_index, target in enumerate(target_rectangles):
            if source.get("label") != target.get("label"):
                continue
            score = iou(source["rect"], target["rect"])
            if score >= threshold:
                candidates.append((score, source_index, target_index))

    candidates.sort(key=lambda item: item[0], reverse=True)
    used_source = set()
    used_target = set()
    matches: List[Tuple[int, int, float]] = []
    for score, source_index, target_index in candidates:
        if source_index in used_source or target_index in used_target:
            continue
        used_source.add(source_index)
        used_target.add(target_index)
        matches.append((source_index, target_index, score))
    return matches


def smallest_containing_rectangle(
    point: Point,
    rectangles: Iterable[Tuple[int, Rect]],
) -> int:
    """返回包含 point 且面积最小的矩形索引；找不到时返回 -1。"""
    candidates = [
        (rect_area(rect), index)
        for index, rect in rectangles
        if point_in_rect(point, rect)
    ]
    if not candidates:
        return -1
    candidates.sort()
    return candidates[0][1]
