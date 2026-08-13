"""X-AnyLabeling/LabelMe JSON 的读取、保存与关键点关联。"""

import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image

from core import (
    greedy_iou_match,
    point_from_relative,
    rect_bounds,
    relative_position,
    smallest_containing_rectangle,
)


SUGGESTED_KEYPOINT_FLAG = "bee_keypoint_suggested"
REVIEW_REQUIRED_FLAG = "bee_keypoint_review_required"


def json_path_for_image(image_path: Path) -> Path:
    return image_path.with_suffix(".json")


def load_document(image_path: Path) -> Dict:
    json_path = json_path_for_image(image_path)
    if json_path.exists():
        with json_path.open("r", encoding="utf-8-sig") as file:
            document = json.load(file)
        document.setdefault("shapes", [])
        document.setdefault("flags", {})
        document.setdefault("description", "")
        return document

    with Image.open(image_path) as image:
        width, height = image.size
    return {
        "version": "3.3.10",
        "flags": {},
        "shapes": [],
        "imagePath": image_path.name,
        "imageData": None,
        "imageHeight": height,
        "imageWidth": width,
        "description": "",
    }


def save_document(image_path: Path, document: Dict, create_backup: bool = True) -> Path:
    """原子写入 JSON；首次覆盖原文件前创建 .json.bak。"""
    json_path = json_path_for_image(image_path)
    document["imagePath"] = image_path.name

    if create_backup and json_path.exists():
        backup_path = json_path.with_suffix(json_path.suffix + ".bak")
        if not backup_path.exists():
            shutil.copy2(json_path, backup_path)

    temporary_path = json_path.with_name("." + json_path.name + ".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(document, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(str(temporary_path), str(json_path))
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return json_path


def rectangle_records(document: Dict) -> List[Dict]:
    records = []
    for shape_index, shape in enumerate(document.get("shapes", [])):
        if shape.get("shape_type") != "rectangle":
            continue
        try:
            bounds = rect_bounds(shape.get("points", []))
        except (TypeError, ValueError, IndexError):
            continue
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        records.append(
            {
                "shape_index": shape_index,
                "shape": shape,
                "rect": bounds,
                "label": str(shape.get("label", "")),
                "group_id": shape.get("group_id"),
            }
        )
    return records


def point_records(document: Dict) -> List[Dict]:
    records = []
    for shape_index, shape in enumerate(document.get("shapes", [])):
        if shape.get("shape_type") != "point":
            continue
        points = shape.get("points") or []
        if not points or len(points[0]) < 2:
            continue
        records.append(
            {
                "shape_index": shape_index,
                "shape": shape,
                "point": (float(points[0][0]), float(points[0][1])),
                "label": str(shape.get("label", "")),
                "group_id": shape.get("group_id"),
            }
        )
    return records


def collect_point_labels(document: Dict) -> List[str]:
    labels = []
    for record in point_records(document):
        if record["label"] and record["label"] not in labels:
            labels.append(record["label"])
    return labels


def rectangle_position_for_point(
    document: Dict,
    point_shape: Dict,
    rectangles: Optional[Sequence[Dict]] = None,
) -> int:
    """返回点对应的 rectangle_records 位置。"""
    rectangles = list(rectangles if rectangles is not None else rectangle_records(document))
    point_group = point_shape.get("group_id")
    if point_group is not None:
        grouped = [
            position
            for position, rectangle in enumerate(rectangles)
            if rectangle["shape"].get("group_id") == point_group
        ]
        if len(grouped) == 1:
            return grouped[0]

    points = point_shape.get("points") or []
    if not points:
        return -1
    point = (float(points[0][0]), float(points[0][1]))
    return smallest_containing_rectangle(
        point,
        ((position, rectangle["rect"]) for position, rectangle in enumerate(rectangles)),
    )


def next_group_id(document: Dict) -> int:
    numeric_ids = []
    for shape in document.get("shapes", []):
        group_id = shape.get("group_id")
        if isinstance(group_id, int) and not isinstance(group_id, bool):
            numeric_ids.append(group_id)
    return max(numeric_ids, default=0) + 1


def ensure_rectangle_group_id(document: Dict, rectangle_position: int) -> int:
    rectangles = rectangle_records(document)
    rectangle = rectangles[rectangle_position]["shape"]
    group_id = rectangle.get("group_id")
    if group_id is None:
        group_id = next_group_id(document)
        rectangle["group_id"] = group_id
    return group_id


def set_rectangle_bounds(
    document: Dict,
    rectangle_position: int,
    bounds: Tuple[float, float, float, float],
) -> bool:
    """更新矩形坐标，保留标签、Track ID、属性和关键点。"""
    rectangles = rectangle_records(document)
    if not 0 <= rectangle_position < len(rectangles):
        return False
    x1, y1, x2, y2 = (float(value) for value in bounds)
    if x2 <= x1 or y2 <= y1:
        return False
    rectangles[rectangle_position]["shape"]["points"] = [
        [x1, y1],
        [x2, y1],
        [x2, y2],
        [x1, y2],
    ]
    return True


def make_point_shape(
    label: str,
    point: Tuple[float, float],
    group_id,
    suggested: bool = False,
) -> Dict:
    return {
        "kie_linking": [],
        "label": label,
        "score": None,
        "points": [[float(point[0]), float(point[1])]],
        "group_id": group_id,
        "description": "",
        "difficult": False,
        "shape_type": "point",
        "flags": {SUGGESTED_KEYPOINT_FLAG: True} if suggested else {},
        "attributes": {},
    }


def keypoints_for_rectangle(document: Dict, rectangle_position: int) -> List[Dict]:
    rectangles = rectangle_records(document)
    if not 0 <= rectangle_position < len(rectangles):
        return []
    return keypoints_by_rectangle(document, rectangles).get(rectangle_position, [])


def keypoints_by_rectangle(
    document: Dict, rectangles: Optional[Sequence[Dict]] = None
) -> Dict[int, List[Dict]]:
    """一次扫描全部关键点，并按所属检测框位置分组。"""
    rectangles = list(rectangles if rectangles is not None else rectangle_records(document))
    grouped_positions: Dict[object, List[int]] = {}
    for position, rectangle in enumerate(rectangles):
        group_id = rectangle["shape"].get("group_id")
        if group_id is not None:
            grouped_positions.setdefault(group_id, []).append(position)

    result: Dict[int, List[Dict]] = {}
    for record in point_records(document):
        point_group = record["shape"].get("group_id")
        positions = grouped_positions.get(point_group, []) if point_group is not None else []
        if len(positions) == 1:
            parent = positions[0]
        else:
            parent = smallest_containing_rectangle(
                record["point"],
                (
                    (position, rectangle["rect"])
                    for position, rectangle in enumerate(rectangles)
                ),
            )
        if parent >= 0:
            result.setdefault(parent, []).append(record)
    return result


def add_or_replace_point(
    document: Dict,
    rectangle_position: int,
    label: str,
    point: Tuple[float, float],
    replace_same_label: bool = True,
    suggested: bool = False,
) -> bool:
    """为指定框添加点。返回是否修改了 document。"""
    rectangles = rectangle_records(document)
    if not 0 <= rectangle_position < len(rectangles):
        return False

    if replace_same_label:
        removable_ids = set()
        for record in point_records(document):
            if record["label"] != label:
                continue
            parent = rectangle_position_for_point(document, record["shape"], rectangles)
            if parent == rectangle_position:
                removable_ids.add(id(record["shape"]))
        if removable_ids:
            document["shapes"] = [
                shape for shape in document["shapes"] if id(shape) not in removable_ids
            ]

    group_id = ensure_rectangle_group_id(document, rectangle_position)
    document["shapes"].append(
        make_point_shape(label, point, group_id, suggested=suggested)
    )
    return True


def point_is_suggested(record_or_shape: Dict) -> bool:
    shape = record_or_shape.get("shape", record_or_shape)
    return bool(shape.get("flags", {}).get(SUGGESTED_KEYPOINT_FLAG, False))


def rectangle_review_is_pending(document: Dict, rectangle_position: int) -> bool:
    rectangles = rectangle_records(document)
    if not 0 <= rectangle_position < len(rectangles):
        return False
    flags = rectangles[rectangle_position]["shape"].get("flags", {})
    if REVIEW_REQUIRED_FLAG in flags:
        return bool(flags[REVIEW_REQUIRED_FLAG])
    return any(
        point_is_suggested(record)
        for record in keypoints_for_rectangle(document, rectangle_position)
    )


def review_progress(document: Dict) -> Dict[str, int]:
    rectangles = rectangle_records(document)
    points_by_rectangle = keypoints_by_rectangle(document, rectangles)
    total = 0
    pending = 0
    for position, rectangle in enumerate(rectangles):
        flags = rectangle["shape"].get("flags", {})
        legacy_pending = any(
            point_is_suggested(record)
            for record in points_by_rectangle.get(position, [])
        )
        if REVIEW_REQUIRED_FLAG not in flags and not legacy_pending:
            continue
        total += 1
        if bool(flags.get(REVIEW_REQUIRED_FLAG, legacy_pending)):
            pending += 1
    return {"reviewed": total - pending, "pending": pending, "total": total}


def confirm_keypoints_for_rectangle(document: Dict, rectangle_position: int) -> int:
    """将当前框的自动传播点标记为人工确认。"""
    was_pending = rectangle_review_is_pending(document, rectangle_position)
    changed = 0
    for record in keypoints_for_rectangle(document, rectangle_position):
        flags = record["shape"].setdefault("flags", {})
        if flags.pop(SUGGESTED_KEYPOINT_FLAG, None):
            changed += 1
    rectangles = rectangle_records(document)
    if was_pending and 0 <= rectangle_position < len(rectangles):
        rectangles[rectangle_position]["shape"].setdefault("flags", {})[
            REVIEW_REQUIRED_FLAG
        ] = False
        if changed == 0:
            changed = 1
    return changed


def swap_keypoint_labels(
    document: Dict,
    rectangle_position: int,
    first_label: str = "head",
    second_label: str = "tail",
) -> bool:
    """交换当前框中一对关键点标签，并把它们视为人工确认。"""
    rectangles = rectangle_records(document)
    if not 0 <= rectangle_position < len(rectangles):
        return False
    was_pending = rectangle_review_is_pending(document, rectangle_position)
    records = keypoints_for_rectangle(document, rectangle_position)
    first = [record for record in records if record["label"] == first_label]
    second = [record for record in records if record["label"] == second_label]
    if len(first) != 1 or len(second) != 1:
        return False
    first[0]["shape"]["label"] = second_label
    second[0]["shape"]["label"] = first_label
    for record in (first[0], second[0]):
        record["shape"].setdefault("flags", {}).pop(
            SUGGESTED_KEYPOINT_FLAG, None
        )
    if was_pending:
        rectangles[rectangle_position]["shape"].setdefault("flags", {})[
            REVIEW_REQUIRED_FLAG
        ] = False
    return True


def delete_points(
    document: Dict,
    rectangle_position: int,
    label: Optional[str] = None,
) -> int:
    rectangles = rectangle_records(document)
    if not 0 <= rectangle_position < len(rectangles):
        return 0

    removable_ids = set()
    for record in point_records(document):
        if label is not None and record["label"] != label:
            continue
        parent = rectangle_position_for_point(document, record["shape"], rectangles)
        if parent == rectangle_position:
            removable_ids.add(id(record["shape"]))

    if removable_ids:
        document["shapes"] = [
            shape for shape in document["shapes"] if id(shape) not in removable_ids
        ]
    return len(removable_ids)


def delete_nearest_point(
    document: Dict,
    rectangle_position: int,
    point: Tuple[float, float],
    max_distance: Optional[float] = None,
) -> Optional[str]:
    candidates = keypoints_for_rectangle(document, rectangle_position)
    if not candidates:
        return None
    nearest = min(
        candidates,
        key=lambda record: (record["point"][0] - point[0]) ** 2
        + (record["point"][1] - point[1]) ** 2,
    )
    if max_distance is not None:
        distance_squared = (
            (nearest["point"][0] - point[0]) ** 2
            + (nearest["point"][1] - point[1]) ** 2
        )
        if distance_squared > max_distance**2:
            return None
    document["shapes"] = [
        shape for shape in document["shapes"] if shape is not nearest["shape"]
    ]
    return nearest["label"]


def rename_point_label(document: Dict, old_label: str, new_label: str) -> int:
    changed = 0
    for record in point_records(document):
        if record["label"] == old_label:
            record["shape"]["label"] = new_label
            changed += 1
    return changed


def build_transfer_plan(
    source_document: Dict,
    target_document: Dict,
    threshold: float,
    overwrite_same_label: bool = False,
) -> Dict:
    source_rectangles = rectangle_records(source_document)
    target_rectangles = rectangle_records(target_document)
    matches = greedy_iou_match(source_rectangles, target_rectangles, threshold)

    source_points_by_rectangle: Dict[int, List[Dict]] = {}
    for record in point_records(source_document):
        parent = rectangle_position_for_point(
            source_document, record["shape"], source_rectangles
        )
        if parent >= 0:
            source_points_by_rectangle.setdefault(parent, []).append(record)

    target_labels_by_rectangle: Dict[int, set] = {}
    for record in point_records(target_document):
        parent = rectangle_position_for_point(
            target_document, record["shape"], target_rectangles
        )
        if parent >= 0:
            target_labels_by_rectangle.setdefault(parent, set()).add(record["label"])

    actions = []
    skipped_existing = 0
    for source_position, target_position, score in matches:
        source_rect = source_rectangles[source_position]["rect"]
        target_rect = target_rectangles[target_position]["rect"]
        for point_record in source_points_by_rectangle.get(source_position, []):
            label = point_record["label"]
            if (
                not overwrite_same_label
                and label in target_labels_by_rectangle.get(target_position, set())
            ):
                skipped_existing += 1
                continue
            relative = relative_position(point_record["point"], source_rect)
            target_point = point_from_relative(relative, target_rect)
            actions.append(
                {
                    "source_rectangle": source_position,
                    "target_rectangle": target_position,
                    "label": label,
                    "point": target_point,
                    "iou": score,
                }
            )

    scores = [match[2] for match in matches]
    return {
        "matches": matches,
        "actions": actions,
        "source_rectangle_count": len(source_rectangles),
        "target_rectangle_count": len(target_rectangles),
        "source_keypoint_count": sum(len(value) for value in source_points_by_rectangle.values()),
        "skipped_existing": skipped_existing,
        "minimum_iou": min(scores) if scores else 0.0,
        "maximum_iou": max(scores) if scores else 0.0,
        "average_iou": sum(scores) / len(scores) if scores else 0.0,
    }


def build_trackid_transfer_plan(
    source_document: Dict,
    target_document: Dict,
    group_id,
    overwrite_suggested: bool = True,
) -> Dict:
    """按相同 Track ID 迁移关键点；人工确认点永不覆盖。"""
    source_rectangles = rectangle_records(source_document)
    target_rectangles = rectangle_records(target_document)
    source_positions = [
        position
        for position, rectangle in enumerate(source_rectangles)
        if rectangle["group_id"] == group_id
    ]
    target_positions = [
        position
        for position, rectangle in enumerate(target_rectangles)
        if rectangle["group_id"] == group_id
    ]
    if len(source_positions) != 1 or len(target_positions) != 1:
        return {
            "group_id": group_id,
            "source_found": len(source_positions) == 1,
            "target_found": len(target_positions) == 1,
            "actions": [],
            "skipped_existing": 0,
            "replaced_suggested": 0,
        }

    source_position = source_positions[0]
    target_position = target_positions[0]
    source_points = keypoints_by_rectangle(
        source_document, source_rectangles
    ).get(source_position, [])
    target_points = keypoints_by_rectangle(
        target_document, target_rectangles
    ).get(target_position, [])
    target_by_label: Dict[str, List[Dict]] = {}
    for record in target_points:
        target_by_label.setdefault(record["label"], []).append(record)

    actions = []
    skipped_existing = 0
    replaced_suggested = 0
    source_rect = source_rectangles[source_position]["rect"]
    target_rect = target_rectangles[target_position]["rect"]
    for source_point in source_points:
        label = source_point["label"]
        existing = target_by_label.get(label, [])
        replace_same_label = False
        if existing:
            if overwrite_suggested and all(point_is_suggested(item) for item in existing):
                replace_same_label = True
                replaced_suggested += len(existing)
            else:
                skipped_existing += 1
                continue
        relative = relative_position(source_point["point"], source_rect)
        actions.append(
            {
                "source_rectangle": source_position,
                "target_rectangle": target_position,
                "label": label,
                "point": point_from_relative(relative, target_rect),
                "replace_same_label": replace_same_label,
                "suggested": True,
            }
        )
    return {
        "group_id": group_id,
        "source_found": True,
        "target_found": True,
        "actions": actions,
        "skipped_existing": skipped_existing,
        "replaced_suggested": replaced_suggested,
    }


def build_next_frame_transfer_plan(
    source_document: Dict,
    target_document: Dict,
    overwrite_suggested: bool = True,
) -> Dict:
    """把源帧所有唯一 Track ID 的关键点迁移到目标帧同 ID 框。"""
    source_rectangles = rectangle_records(source_document)
    target_rectangles = rectangle_records(target_document)
    source_group_ids = []
    for rectangle in source_rectangles:
        group_id = rectangle["group_id"]
        if group_id is not None and group_id not in source_group_ids:
            source_group_ids.append(group_id)

    target_group_ids = {
        rectangle["group_id"]
        for rectangle in target_rectangles
        if rectangle["group_id"] is not None
    }
    actions = []
    matched_tracks = 0
    tracks_with_keypoints = 0
    skipped_existing = 0
    replaced_suggested = 0
    for group_id in source_group_ids:
        plan = build_trackid_transfer_plan(
            source_document,
            target_document,
            group_id,
            overwrite_suggested=overwrite_suggested,
        )
        if plan["source_found"] and plan["target_found"]:
            matched_tracks += 1
            if plan["actions"] or plan["skipped_existing"]:
                tracks_with_keypoints += 1
        actions.extend(plan["actions"])
        skipped_existing += plan["skipped_existing"]
        replaced_suggested += plan["replaced_suggested"]

    return {
        "actions": actions,
        "source_track_count": len(source_group_ids),
        "target_track_count": len(target_group_ids),
        "matched_tracks": matched_tracks,
        "tracks_with_keypoints": tracks_with_keypoints,
        "skipped_existing": skipped_existing,
        "replaced_suggested": replaced_suggested,
    }


def apply_transfer_plan(
    target_document: Dict,
    plan: Dict,
    overwrite_same_label: bool,
    suggested: bool = False,
) -> int:
    applied = 0
    target_rectangles = rectangle_records(target_document)
    for action in plan.get("actions", []):
        action_suggested = action.get("suggested", suggested)
        if add_or_replace_point(
            target_document,
            action["target_rectangle"],
            action["label"],
            action["point"],
            replace_same_label=action.get(
                "replace_same_label", overwrite_same_label
            ),
            suggested=action_suggested,
        ):
            applied += 1
            target_position = action["target_rectangle"]
            if action_suggested and 0 <= target_position < len(target_rectangles):
                target_rectangles[target_position]["shape"].setdefault(
                    "flags", {}
                )[REVIEW_REQUIRED_FLAG] = True
    return applied
