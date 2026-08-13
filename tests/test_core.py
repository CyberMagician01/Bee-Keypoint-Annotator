import copy
import unittest

from annotation_io import (
    add_or_replace_point,
    apply_transfer_plan,
    build_next_frame_transfer_plan,
    build_trackid_transfer_plan,
    build_transfer_plan,
    confirm_keypoints_for_rectangle,
    delete_nearest_point,
    keypoints_by_rectangle,
    keypoints_for_rectangle,
    point_is_suggested,
    rectangle_records,
    rectangle_review_is_pending,
    review_progress,
    set_rectangle_bounds,
    swap_keypoint_labels,
)
from core import (
    adjust_rectangle_bounds,
    greedy_iou_match,
    iou,
    point_from_relative,
    rect_bounds,
    rectangle_handle_mode,
    rectangle_drag_mode,
    relative_position,
    symmetric_point,
)


def rectangle(label, x1, y1, x2, y2, group_id=None):
    return {
        "label": label,
        "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        "group_id": group_id,
        "shape_type": "rectangle",
        "flags": {},
    }


class CoreGeometryTests(unittest.TestCase):
    def test_rect_bounds_accepts_four_points(self):
        self.assertEqual(
            rect_bounds([[10, 20], [30, 20], [30, 50], [10, 50]]),
            (10.0, 20.0, 30.0, 50.0),
        )

    def test_iou(self):
        score = iou((0, 0, 10, 10), (5, 5, 15, 15))
        self.assertAlmostEqual(score, 25 / 175)

    def test_center_symmetry(self):
        self.assertEqual(symmetric_point((2, 4), (0, 0, 10, 10)), (8, 6))

    def test_adjustable_symmetry_ratio(self):
        rect = (0, 0, 10, 10)
        self.assertEqual(symmetric_point((2, 4), rect, ratio=0.5), (6.5, 5.5))
        self.assertEqual(symmetric_point((2, 4), rect, ratio=1.5), (9.5, 6.5))
        self.assertEqual(
            symmetric_point((0, 0), rect, ratio=2.0, clamp_to_rect=True),
            (10, 10),
        )

    def test_relative_round_trip(self):
        source = (10, 20, 30, 60)
        point = (15, 30)
        relative = relative_position(point, source)
        self.assertEqual(relative, (0.25, 0.25))
        self.assertEqual(point_from_relative(relative, (100, 200, 140, 280)), (110, 220))

    def test_greedy_match_is_one_to_one(self):
        source = [
            {"label": "bee", "rect": (0, 0, 10, 10)},
            {"label": "bee", "rect": (20, 0, 30, 10)},
        ]
        target = [
            {"label": "bee", "rect": (1, 0, 11, 10)},
            {"label": "bee", "rect": (21, 0, 31, 10)},
        ]
        matches = greedy_iou_match(source, target, 0.5)
        self.assertEqual([(item[0], item[1]) for item in matches], [(0, 0), (1, 1)])

    def test_rectangle_drag_hit_testing(self):
        rect = (10, 20, 30, 50)
        self.assertEqual(rectangle_drag_mode((20, 35), rect, 2), "move")
        self.assertEqual(rectangle_drag_mode((10, 20), rect, 2), "nw")
        self.assertEqual(rectangle_drag_mode((30, 35), rect, 2), "e")
        self.assertEqual(rectangle_drag_mode((5, 5), rect, 2), "")

    def test_rectangle_handle_hit_testing_uses_eight_points_only(self):
        rect = (10, 20, 30, 50)
        self.assertEqual(rectangle_handle_mode((10, 20), rect, 2), "nw")
        self.assertEqual(rectangle_handle_mode((20, 20), rect, 2), "n")
        self.assertEqual(rectangle_handle_mode((30, 35), rect, 2), "e")
        self.assertEqual(rectangle_handle_mode((20, 50), rect, 2), "s")
        self.assertEqual(rectangle_handle_mode((10, 25), rect, 2), "")

    def test_rectangle_move_is_clamped_to_image(self):
        moved = adjust_rectangle_bounds(
            (10, 20, 30, 50),
            (15, 25),
            (-20, -30),
            "move",
            (100, 80),
        )
        self.assertEqual(moved, (0.0, 0.0, 20.0, 30.0))

    def test_rectangle_resize_respects_minimum_size_and_image_bounds(self):
        resized = adjust_rectangle_bounds(
            (10, 20, 30, 50),
            (30, 50),
            (200, 200),
            "se",
            (100, 80),
        )
        self.assertEqual(resized, (10, 20, 100.0, 80.0))
        minimum = adjust_rectangle_bounds(
            (10, 20, 30, 50),
            (10, 20),
            (100, 100),
            "nw",
            (100, 80),
            minimum_size=6,
        )
        self.assertEqual(minimum, (24, 44, 30, 50))


class AnnotationTransferTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "shapes": [
                rectangle("bee", 0, 0, 10, 20),
                rectangle("bee", 30, 0, 40, 20),
            ]
        }
        self.target = {
            "shapes": [
                rectangle("bee", 1, 0, 11, 20),
                rectangle("bee", 31, 0, 41, 20),
            ]
        }

    def test_point_is_linked_to_rectangle(self):
        add_or_replace_point(self.source, 0, "head", (2, 4))
        records = keypoints_for_rectangle(self.source, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["label"], "head")
        self.assertEqual(
            rectangle_records(self.source)[0]["shape"]["group_id"],
            records[0]["shape"]["group_id"],
        )

    def test_keypoints_are_grouped_in_one_pass(self):
        add_or_replace_point(self.source, 0, "head", (2, 4))
        add_or_replace_point(self.source, 1, "tail", (38, 16))
        grouped = keypoints_by_rectangle(self.source)
        self.assertEqual([record["label"] for record in grouped[0]], ["head"])
        self.assertEqual([record["label"] for record in grouped[1]], ["tail"])

    def test_transfer_preserves_relative_position(self):
        add_or_replace_point(self.source, 0, "head", (2, 4))
        add_or_replace_point(self.source, 0, "tail", (8, 16))
        plan = build_transfer_plan(self.source, self.target, 0.5)
        self.assertEqual(len(plan["matches"]), 2)
        self.assertEqual(len(plan["actions"]), 2)
        applied = apply_transfer_plan(self.target, plan, overwrite_same_label=False)
        self.assertEqual(applied, 2)
        points = {
            record["label"]: record["point"]
            for record in keypoints_for_rectangle(self.target, 0)
        }
        self.assertEqual(points["head"], (3.0, 4.0))
        self.assertEqual(points["tail"], (9.0, 16.0))

    def test_default_transfer_does_not_overwrite_existing_label(self):
        add_or_replace_point(self.source, 0, "head", (2, 4))
        add_or_replace_point(self.target, 0, "head", (5, 5))
        plan = build_transfer_plan(
            self.source, self.target, 0.5, overwrite_same_label=False
        )
        self.assertEqual(plan["skipped_existing"], 1)
        self.assertEqual(len(plan["actions"]), 0)

    def test_overwrite_replaces_existing_label(self):
        add_or_replace_point(self.source, 0, "head", (2, 4))
        add_or_replace_point(self.target, 0, "head", (5, 5))
        plan = build_transfer_plan(
            self.source, self.target, 0.5, overwrite_same_label=True
        )
        apply_transfer_plan(self.target, plan, overwrite_same_label=True)
        points = keypoints_for_rectangle(self.target, 0)
        self.assertEqual(len([point for point in points if point["label"] == "head"]), 1)
        self.assertEqual(points[0]["point"], (3.0, 4.0))

    def test_middle_click_style_delete_requires_nearby_point(self):
        add_or_replace_point(self.source, 0, "head", (2, 4))
        deleted = delete_nearest_point(
            self.source, 0, (9, 18), max_distance=2.0
        )
        self.assertIsNone(deleted)
        self.assertEqual(len(keypoints_for_rectangle(self.source, 0)), 1)
        deleted = delete_nearest_point(
            self.source, 0, (2.5, 4.5), max_distance=2.0
        )
        self.assertEqual(deleted, "head")
        self.assertEqual(len(keypoints_for_rectangle(self.source, 0)), 0)

    def test_rectangle_update_preserves_track_id_and_keypoint_coordinates(self):
        self.source["shapes"][0]["group_id"] = 19
        add_or_replace_point(self.source, 0, "head", (2, 4))
        self.assertTrue(set_rectangle_bounds(self.source, 0, (5, 6, 20, 30)))
        rectangle_record = rectangle_records(self.source)[0]
        self.assertEqual(rectangle_record["rect"], (5.0, 6.0, 20.0, 30.0))
        self.assertEqual(rectangle_record["group_id"], 19)
        point = keypoints_for_rectangle(self.source, 0)[0]
        self.assertEqual(point["point"], (2.0, 4.0))
        self.assertEqual(point["group_id"], 19)


class TrackIdTransferTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "shapes": [
                rectangle("bee", 0, 0, 10, 20, group_id=7),
                rectangle("bee", 30, 0, 40, 20, group_id=8),
            ]
        }
        self.target = {
            "shapes": [
                rectangle("bee", 100, 100, 120, 140, group_id=7),
                rectangle("bee", 150, 100, 170, 140, group_id=8),
            ]
        }
        add_or_replace_point(self.source, 0, "head", (2, 4))
        add_or_replace_point(self.source, 0, "tail", (8, 16))

    def test_track_id_transfer_uses_relative_position_and_marks_suggested(self):
        plan = build_trackid_transfer_plan(self.source, self.target, 7)
        self.assertEqual(len(plan["actions"]), 2)
        self.assertEqual(apply_transfer_plan(self.target, plan, False), 2)

        points = {
            record["label"]: record
            for record in keypoints_for_rectangle(self.target, 0)
        }
        self.assertEqual(points["head"]["point"], (104.0, 108.0))
        self.assertEqual(points["tail"]["point"], (116.0, 132.0))
        self.assertTrue(all(point_is_suggested(record) for record in points.values()))
        self.assertEqual(keypoints_for_rectangle(self.target, 1), [])

    def test_next_frame_transfer_copies_all_matching_track_ids(self):
        add_or_replace_point(self.source, 1, "head", (32, 4))
        add_or_replace_point(self.source, 1, "tail", (38, 16))

        plan = build_next_frame_transfer_plan(self.source, self.target)
        self.assertEqual(plan["matched_tracks"], 2)
        self.assertEqual(len(plan["actions"]), 4)
        self.assertEqual(apply_transfer_plan(self.target, plan, False), 4)

        first = {
            record["label"]: record["point"]
            for record in keypoints_for_rectangle(self.target, 0)
        }
        second = {
            record["label"]: record["point"]
            for record in keypoints_for_rectangle(self.target, 1)
        }
        self.assertEqual(first, {"head": (104.0, 108.0), "tail": (116.0, 132.0)})
        self.assertEqual(second, {"head": (154.0, 108.0), "tail": (166.0, 132.0)})

    def test_next_frame_transfer_skips_unmatched_track_id(self):
        self.target["shapes"][1]["group_id"] = 99
        add_or_replace_point(self.source, 1, "head", (32, 4))

        plan = build_next_frame_transfer_plan(self.source, self.target)
        self.assertEqual(plan["matched_tracks"], 1)
        self.assertEqual(len(plan["actions"]), 2)

    def test_track_id_transfer_never_overwrites_confirmed_point(self):
        add_or_replace_point(self.target, 0, "head", (111, 111))
        plan = build_trackid_transfer_plan(self.source, self.target, 7)
        self.assertEqual(plan["skipped_existing"], 1)
        apply_transfer_plan(self.target, plan, False)

        points = {
            record["label"]: record
            for record in keypoints_for_rectangle(self.target, 0)
        }
        self.assertEqual(points["head"]["point"], (111.0, 111.0))
        self.assertFalse(point_is_suggested(points["head"]))
        self.assertTrue(point_is_suggested(points["tail"]))

    def test_new_suggestion_replaces_old_suggestion(self):
        add_or_replace_point(
            self.target, 0, "head", (110, 110), suggested=True
        )
        plan = build_trackid_transfer_plan(self.source, self.target, 7)
        self.assertEqual(plan["replaced_suggested"], 1)
        apply_transfer_plan(self.target, plan, False)

        head = next(
            record
            for record in keypoints_for_rectangle(self.target, 0)
            if record["label"] == "head"
        )
        self.assertEqual(head["point"], (104.0, 108.0))
        self.assertTrue(point_is_suggested(head))

    def test_confirm_clears_suggested_state(self):
        plan = build_trackid_transfer_plan(self.source, self.target, 7)
        apply_transfer_plan(self.target, plan, False)
        self.assertEqual(
            review_progress(self.target),
            {"reviewed": 0, "pending": 1, "total": 1},
        )
        self.assertEqual(confirm_keypoints_for_rectangle(self.target, 0), 2)
        self.assertTrue(
            all(
                not point_is_suggested(record)
                for record in keypoints_for_rectangle(self.target, 0)
            )
        )
        self.assertEqual(
            review_progress(self.target),
            {"reviewed": 1, "pending": 0, "total": 1},
        )

    def test_manual_correction_stays_pending_until_rectangle_is_confirmed(self):
        plan = build_trackid_transfer_plan(self.source, self.target, 7)
        apply_transfer_plan(self.target, plan, False)
        add_or_replace_point(self.target, 0, "head", (105, 109))

        self.assertTrue(rectangle_review_is_pending(self.target, 0))
        confirm_keypoints_for_rectangle(self.target, 0)
        self.assertFalse(rectangle_review_is_pending(self.target, 0))

    def test_swap_head_tail_also_confirms_points(self):
        plan = build_trackid_transfer_plan(self.source, self.target, 7)
        apply_transfer_plan(self.target, plan, False)
        self.assertTrue(swap_keypoint_labels(self.target, 0))
        points = {
            record["label"]: record
            for record in keypoints_for_rectangle(self.target, 0)
        }
        self.assertEqual(points["head"]["point"], (116.0, 132.0))
        self.assertEqual(points["tail"]["point"], (104.0, 108.0))
        self.assertTrue(all(not point_is_suggested(record) for record in points.values()))
        self.assertEqual(
            review_progress(self.target),
            {"reviewed": 1, "pending": 0, "total": 1},
        )

    def test_manual_swap_does_not_create_review_progress(self):
        add_or_replace_point(self.target, 0, "head", (105, 109))
        add_or_replace_point(self.target, 0, "tail", (115, 131))
        self.assertTrue(swap_keypoint_labels(self.target, 0))
        self.assertEqual(
            review_progress(self.target),
            {"reviewed": 0, "pending": 0, "total": 0},
        )


if __name__ == "__main__":
    unittest.main()
