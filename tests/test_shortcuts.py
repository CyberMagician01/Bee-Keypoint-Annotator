import copy
import queue
import tkinter as tk
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from annotation_io import (
    REVIEW_REQUIRED_FLAG,
    add_or_replace_point,
    keypoints_for_rectangle,
    point_is_suggested,
    rectangle_records,
    rectangle_review_is_pending,
)
from app import BeeKeypointAnnotator, DEFAULT_SHORTCUTS, win32gui


def rectangle(label, x1, y1, x2, y2, group_id=None):
    return {
        "label": label,
        "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        "group_id": group_id,
        "shape_type": "rectangle",
        "flags": {},
    }


class FakeVariable:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeAfterRoot:
    def __init__(self):
        self.callback = None
        self.delay = None
        self.cancelled = []

    def after(self, delay, callback):
        self.delay = delay
        self.callback = callback
        return "after-job"

    def after_cancel(self, job):
        self.cancelled.append(job)


class FakeCanvas:
    def __init__(self, width=200, height=100):
        self.width = width
        self.height = height
        self.cursor = None

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def configure(self, **kwargs):
        self.cursor = kwargs.get("cursor", self.cursor)


class ShortcutTests(unittest.TestCase):
    def test_long_press_delay_is_clamped_and_rounded(self):
        normalize = BeeKeypointAnnotator._normalize_long_press_delay
        self.assertEqual(normalize(280), 300)
        self.assertEqual(normalize(20), 100)
        self.assertEqual(normalize(5000), 1000)
        self.assertEqual(normalize("invalid"), 250)

    def test_quick_left_click_keeps_keypoint_action(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.root = FakeAfterRoot()
        app.pointer_press = None
        app.rectangle_drag = None
        app.long_press_delay_ms = 250
        app.status_var = FakeVariable()
        app._current_image_path = lambda: Path("frame.jpg")
        app._rectangle_drag_candidate_at = lambda *_args: (-1, "")
        clicks = []
        app._on_detail_click = clicks.append
        event = type("Event", (), {"x": 20, "y": 30, "state": 0})()

        self.assertEqual(app._on_pointer_press(event, "detail"), "break")
        self.assertEqual(app.root.delay, 250)
        self.assertEqual(app._on_pointer_release(event, "detail"), "break")

        self.assertEqual(clicks, [event])
        self.assertEqual(app.root.cancelled, ["after-job"])

    def test_long_press_starts_handle_only_drag_from_press_position(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.root = FakeAfterRoot()
        app.pointer_press = None
        app.rectangle_drag = None
        app.long_press_delay_ms = 300
        app.status_var = FakeVariable()
        app._current_image_path = lambda: Path("frame.jpg")
        app._rectangle_drag_candidate_at = lambda *_args: (-1, "")
        starts = []
        moves = []
        app._begin_rectangle_drag_at = (
            lambda x, y, view, handle_only: starts.append(
                (x, y, view, handle_only)
            )
            or True
        )
        app._continue_rectangle_drag_at = (
            lambda x, y, view: moves.append((x, y, view))
        )
        press = type("Event", (), {"x": 10, "y": 15, "state": 0})()
        motion = type("Event", (), {"x": 40, "y": 45, "state": 0})()

        app._on_pointer_press(press, "detail")
        app._on_pointer_motion(motion, "detail")
        app.root.callback()

        self.assertEqual(starts, [(10.0, 15.0, "detail", True)])
        self.assertEqual(moves, [(40.0, 45.0, "detail")])
        self.assertIsNone(app.pointer_press)

    def test_plain_drag_inside_box_starts_move_without_ctrl_or_long_press(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.root = FakeAfterRoot()
        app.pointer_press = None
        app.rectangle_drag = None
        app.long_press_delay_ms = 300
        app._current_image_path = lambda: Path("frame.jpg")
        app._rectangle_drag_candidate_at = lambda *_args: (1, "move")
        starts = []
        moves = []
        app._begin_rectangle_drag_at = (
            lambda x, y, view, handle_only: starts.append(
                (x, y, view, handle_only)
            )
            or True
        )
        app._continue_rectangle_drag_at = (
            lambda x, y, view: moves.append((x, y, view)) or "break"
        )
        press = type("Event", (), {"x": 10, "y": 15, "state": 0})()
        motion = type("Event", (), {"x": 30, "y": 35, "state": 0})()

        app._on_pointer_press(press, "detail")
        result = app._on_pointer_motion(motion, "detail")

        self.assertEqual(result, "break")
        self.assertEqual(starts, [(10.0, 15.0, "detail", False)])
        self.assertEqual(moves, [(30.0, 35.0, "detail")])
        self.assertEqual(app.root.cancelled, ["after-job"])

    def test_plain_press_on_resize_handle_starts_resize_immediately(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.root = FakeAfterRoot()
        app.pointer_press = None
        app.rectangle_drag = None
        app._rectangle_drag_candidate_at = lambda *_args: (1, "nw")
        starts = []
        app._begin_rectangle_drag_at = (
            lambda x, y, view, handle_only: starts.append(
                (x, y, view, handle_only)
            )
            or True
        )
        event = type("Event", (), {"x": 20, "y": 25, "state": 0})()

        result = app._on_pointer_press(event, "detail")

        self.assertEqual(result, "break")
        self.assertEqual(starts, [(20.0, 25.0, "detail", False)])
        self.assertIsNone(app.pointer_press)

    def test_plain_click_on_other_box_is_forwarded_to_keypoint_action(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.root = FakeAfterRoot()
        app.pointer_press = None
        app.rectangle_drag = None
        app.long_press_delay_ms = 300
        app.current_rectangle_index = 0
        app._current_image_path = lambda: Path("frame.jpg")
        app._rectangle_drag_candidate_at = lambda *_args: (1, "move")
        calls = []
        app._auto_confirm_viewed_rectangle = lambda: calls.append("confirm")
        app._remember_current_rectangle = lambda: calls.append("remember")
        app._refresh_all = lambda: calls.append("refresh")
        app._on_detail_click = lambda _event: calls.append("point")
        app.status_var = FakeVariable()
        event = type("Event", (), {"x": 20, "y": 25, "state": 0})()

        app._on_pointer_press(event, "detail")
        result = app._on_pointer_release(event, "detail")

        self.assertEqual(result, "break")
        self.assertEqual(app.current_rectangle_index, 0)
        self.assertEqual(calls, ["point"])

    def test_detail_click_selects_other_box_and_adds_keypoint_in_one_click(self):
        document = {
            "shapes": [
                rectangle("bee", 0, 0, 10, 10, group_id=1),
                rectangle("bee", 20, 0, 30, 10, group_id=2),
            ]
        }
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.detail_transform = (1, 0, 0)
        app.current_rectangle_index = 0
        app.show_other_boxes = FakeVariable()
        app.show_other_boxes.set(True)
        app.active_label = FakeVariable()
        app.active_label.set("head")
        app.symmetry_enabled = FakeVariable()
        app.symmetry_enabled.set(False)
        app.symmetry_source_label = FakeVariable()
        app.symmetry_target_label = FakeVariable()
        app.symmetry_ratio = FakeVariable()
        app.status_var = FakeVariable()
        app._current_document = lambda: document
        app._current_rectangles = lambda: rectangle_records(document)
        calls = []
        app._auto_confirm_viewed_rectangle = lambda: calls.append("confirm")
        app._remember_current_rectangle = lambda: calls.append("remember")
        app._push_undo = lambda: calls.append("undo")
        app._mark_dirty = lambda: calls.append("dirty")
        app._schedule_autosave = lambda: calls.append("autosave")
        app._refresh_all = lambda: calls.append("refresh")
        event = type("Event", (), {"x": 25, "y": 5, "state": 0})()

        app._on_detail_click(event)

        self.assertEqual(app.current_rectangle_index, 1)
        points = keypoints_for_rectangle(document, 1)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["label"], "head")
        self.assertEqual(points[0]["point"], (25.0, 5.0))
        self.assertEqual(
            calls,
            ["confirm", "remember", "undo", "dirty", "autosave", "refresh"],
        )

    def test_default_side_buttons_switch_rectangles(self):
        self.assertIn("鼠标侧键1", DEFAULT_SHORTCUTS["previous_rectangle"])
        self.assertIn("鼠标侧键2", DEFAULT_SHORTCUTS["next_rectangle"])

    def test_help_uses_f1(self):
        self.assertIn("F1", DEFAULT_SHORTCUTS["open_help"])

    def test_frame_table_uses_g(self):
        self.assertIn("G", DEFAULT_SHORTCUTS["open_frame_table"])

    def test_track_review_shortcuts(self):
        self.assertIn("A", DEFAULT_SHORTCUTS["previous_track_frame"])
        self.assertIn("D", DEFAULT_SHORTCUTS["next_track_frame"])
        self.assertIn("W", DEFAULT_SHORTCUTS["next_track_id"])
        self.assertIn("R", DEFAULT_SHORTCUTS["copy_frame_to_next"])
        self.assertIn("Z", DEFAULT_SHORTCUTS["previous_image"])
        self.assertIn("C", DEFAULT_SHORTCUTS["next_image"])
        self.assertIn("Ctrl+P", DEFAULT_SHORTCUTS["propagate_track"])
        self.assertIn("Space", DEFAULT_SHORTCUTS["confirm_keypoints"])
        self.assertIn("X", DEFAULT_SHORTCUTS["swap_head_tail"])
        self.assertIn("N", DEFAULT_SHORTCUTS["next_review_issue"])
        self.assertIn("V", DEFAULT_SHORTCUTS["start_continuous_annotation"])
        self.assertIn("B", DEFAULT_SHORTCUTS["toggle_track_ids"])
        self.assertIn("F", DEFAULT_SHORTCUTS["toggle_bee_shadow_class"])
        self.assertIn("Y", DEFAULT_SHORTCUTS["toggle_box_class_labels"])
        self.assertEqual(DEFAULT_SHORTCUTS["clear_default_track_id"], ["无", "无"])
        self.assertIn("H", DEFAULT_SHORTCUTS["toggle_continuous_other_boxes"])

    def test_detail_drag_can_target_another_visible_rectangle(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.current_rectangle_index = 0
        rectangles = [
            {"rect": (0, 0, 10, 10)},
            {"rect": (20, 0, 30, 10)},
        ]

        self.assertEqual(
            app._rectangle_drag_target(
                (25, 5), "detail", rectangles, tolerance=1, handle_only=False
            ),
            (1, "move"),
        )
        self.assertEqual(
            app._rectangle_drag_target(
                (20, 0), "detail", rectangles, tolerance=1, handle_only=True
            ),
            (1, "nw"),
        )

    def test_plain_right_click_marks_tail_on_clicked_box(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.detail_transform = (1, 0, 0)
        app.current_rectangle_index = 0
        app._current_document = lambda: {"shapes": []}
        app._current_rectangles = lambda: [{"rect": (0, 0, 10, 10)}]
        app._control_pressed = lambda _event: False
        app.show_other_boxes = FakeVariable()
        app.show_other_boxes.set(True)
        actions = []
        app._apply_detail_right_click_point_action = (
            lambda point, position: actions.append((point, position))
        )
        event = type("Event", (), {"x": 5, "y": 6, "state": 0})()

        self.assertEqual(app._on_detail_right_click(event), "break")
        self.assertEqual(actions, [((5.0, 6.0), 0)])

    def test_ctrl_right_click_opens_menu_for_clicked_other_box(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.detail_transform = (1, 0, 0)
        app.current_rectangle_index = 0
        app._current_document = lambda: {"shapes": []}
        app._current_rectangles = lambda: [
            {"rect": (0, 0, 10, 10)},
            {"rect": (20, 0, 30, 10)},
        ]
        app._control_pressed = lambda _event: True
        app.show_other_boxes = FakeVariable()
        app.show_other_boxes.set(True)
        calls = []
        app._show_detail_rectangle_context_menu = lambda event, point, position: calls.append(
            (event, point, position)
        )
        event = type("Event", (), {"x": 25, "y": 5, "state": 0})()

        self.assertEqual(app._on_detail_right_click(event), "break")
        self.assertEqual(calls, [(event, (25.0, 5.0), 1)])

    def test_context_menu_delete_removes_box_and_points_and_schedules_save(self):
        document = {"shapes": [rectangle("bee", 0, 0, 10, 10, group_id=7)]}
        add_or_replace_point(document, 0, "head", (2, 3))
        add_or_replace_point(document, 0, "tail", (8, 7))
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.current_rectangle_index = 0
        app.overview_static_key = "cached"
        app.status_var = FakeVariable()
        app._current_document = lambda: document
        app._current_rectangles = lambda: rectangle_records(document)
        calls = []
        app._remember_current_rectangle = lambda: calls.append("remember")
        app._push_undo = lambda: calls.append("undo")
        app._discard_last_undo = lambda: calls.append("discard")
        app._mark_dirty = lambda: calls.append("dirty")
        app._schedule_autosave = lambda: calls.append("autosave")
        app._refresh_all = lambda: calls.append("refresh")

        with patch("app.messagebox.askyesno", return_value=True):
            app._delete_rectangle_with_keypoints(0)

        self.assertEqual(document["shapes"], [])
        self.assertEqual(app.current_rectangle_index, -1)
        self.assertIsNone(app.overview_static_key)
        self.assertEqual(
            calls,
            ["remember", "undo", "remember", "dirty", "autosave", "refresh"],
        )

    def test_plain_right_click_adds_tail_even_when_symmetry_is_disabled(self):
        document = {"shapes": [rectangle("bee", 0, 0, 10, 10, group_id=7)]}
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.current_rectangle_index = 0
        app.symmetry_enabled = FakeVariable()
        app.symmetry_enabled.set(False)
        app.symmetry_target_label = FakeVariable()
        app.symmetry_target_label.set("tail")
        app.status_var = FakeVariable()
        app._current_document = lambda: document
        app._current_rectangles = lambda: rectangle_records(document)
        app._remember_current_rectangle = lambda: None
        app._push_undo = lambda: None
        app._mark_dirty = lambda: None
        app._schedule_autosave = lambda: None
        app._refresh_all = lambda: None

        app._apply_detail_right_click_point_action((6, 7), 0)

        points = keypoints_for_rectangle(document, 0)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["label"], "tail")
        self.assertEqual(points[0]["point"], (6.0, 7.0))

    def test_middle_drag_pans_continuous_detail_view(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.detail_transform = (2.0, 0.0, 0.0)
        app.detail_canvas = FakeCanvas(200, 100)
        app.current_pil_image = type("Image", (), {"width": 500, "height": 300})()
        app.current_rectangle_index = 0
        app.continuous_annotation_mode = True
        app.continuous_focus_point = (50.0, 25.0)
        app.middle_pan = None
        app.detail_cache_key = "cached"
        app.status_var = FakeVariable()
        app._current_image_path = lambda: Path("frame.jpg")
        draws = []
        app._draw_detail = lambda: draws.append(True)
        press = type("Event", (), {"x": 100, "y": 50})()
        motion = type("Event", (), {"x": 120, "y": 60})()

        app._on_detail_middle_press(press)
        app._on_detail_middle_motion(motion)

        self.assertEqual(app.continuous_focus_point, (40.0, 20.0))
        self.assertIsNone(app.detail_cache_key)
        self.assertEqual(draws, [True])
        self.assertEqual(app.detail_canvas.cursor, "fleur")

    def test_middle_click_without_drag_keeps_delete_action(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.detail_transform = (1.0, 0.0, 0.0)
        app.detail_canvas = FakeCanvas(200, 100)
        app.current_pil_image = type("Image", (), {"width": 500, "height": 300})()
        app.current_rectangle_index = 0
        app.middle_pan = None
        app.status_var = FakeVariable()
        app._current_image_path = lambda: Path("frame.jpg")
        deleted = []
        app._on_detail_middle_click = lambda event: deleted.append(event)
        event = type("Event", (), {"x": 100, "y": 50})()

        app._on_detail_middle_press(event)
        app._on_detail_middle_release(event)

        self.assertEqual(deleted, [event])
        self.assertEqual(app.detail_canvas.cursor, "crosshair")

    def test_batch_delete_removes_same_id_from_current_and_later_frames(self):
        first = Path("frame_001.jpg")
        second = Path("frame_002.jpg")
        third = Path("frame_003.jpg")
        documents = {
            first: {"shapes": [rectangle("bee", 0, 0, 10, 10, group_id=7)]},
            second: {"shapes": [rectangle("bee", 0, 0, 10, 10, group_id=7)]},
            third: {
                "shapes": [
                    rectangle("bee", 0, 0, 10, 10, group_id=7),
                    rectangle("bee", 20, 0, 30, 10, group_id=8),
                ]
            },
        }
        add_or_replace_point(documents[second], 0, "head", (2, 3))
        add_or_replace_point(documents[third], 0, "tail", (7, 8))
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.images = [first, second, third]
        app.documents = documents
        app.current_image_index = 1
        app.current_rectangle_index = 0
        app.dirty_images = set()
        app.overview_static_key = "cached"
        app.detail_cache_key = "cached"
        app.root = object()
        app.status_var = FakeVariable()
        app._current_image_path = lambda: second
        app._current_document = lambda: documents[second]
        app._current_rectangles = lambda: rectangle_records(documents[second])
        app._get_document = lambda image_path: documents[image_path]
        calls = []
        app._push_undo = lambda image_path=None: calls.append(("undo", image_path))
        app._remember_current_rectangle = lambda: calls.append("remember")
        app._schedule_autosave = lambda: calls.append("autosave")
        app._refresh_all = lambda: calls.append("refresh")

        with patch("app.messagebox.askyesno", return_value=True):
            app._delete_current_and_following_track_boxes(0)

        self.assertEqual(len(rectangle_records(documents[first])), 1)
        self.assertEqual(len(rectangle_records(documents[second])), 0)
        self.assertEqual(
            [record["group_id"] for record in rectangle_records(documents[third])],
            [8],
        )
        self.assertEqual(app.dirty_images, {second, third})
        self.assertEqual(app.current_rectangle_index, -1)
        self.assertEqual(calls[-3:], ["remember", "autosave", "refresh"])

    def test_f_action_toggles_current_box_between_bee_and_beeshadow(self):
        document = {
            "shapes": [rectangle("bee", 0, 0, 10, 10, group_id=7)]
        }
        add_or_replace_point(document, 0, "head", (2, 3))
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.current_rectangle_index = 0
        app.overview_static_key = "cached"
        app.status_var = FakeVariable()
        app._current_document = lambda: document
        app._current_rectangles = lambda: rectangle_records(document)
        calls = []
        app._push_undo = lambda: calls.append("undo")
        app._mark_dirty = lambda: calls.append("dirty")
        app._schedule_autosave = lambda: calls.append("autosave")
        app._refresh_all = lambda: calls.append("refresh")

        app.toggle_current_bee_shadow_class()
        self.assertEqual(rectangle_records(document)[0]["label"], "beeshadow")
        self.assertEqual(rectangle_records(document)[0]["group_id"], 7)
        self.assertEqual(keypoints_for_rectangle(document, 0)[0]["label"], "head")

        app.toggle_current_bee_shadow_class()
        self.assertEqual(rectangle_records(document)[0]["label"], "bee")
        self.assertEqual(
            calls,
            ["undo", "dirty", "autosave", "refresh"] * 2,
        )

    def test_y_toggles_box_class_label_visibility(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.show_box_class_labels = FakeVariable()
        app.show_box_class_labels.set(False)
        app.status_var = FakeVariable()
        calls = []
        app._save_settings = lambda: calls.append("save")
        app._refresh_all = lambda: calls.append("refresh")

        app.toggle_box_class_labels()

        self.assertTrue(app.show_box_class_labels.get())
        self.assertEqual(calls, ["save", "refresh"])
        self.assertIn("已显示", app.status_var.value)

    def test_track_id_normalization_accepts_only_nonnegative_integers(self):
        normalize = BeeKeypointAnnotator._normalize_track_id
        self.assertEqual(normalize("12"), 12)
        self.assertEqual(normalize(3.0), 3)
        self.assertIsNone(normalize("1.5"))
        self.assertIsNone(normalize(-1))
        self.assertIsNone(normalize(True))

    def test_continuous_mode_routes_r_and_e_to_new_workflow(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.continuous_annotation_mode = True
        actions = []
        app.begin_continuous_rectangle_drawing = lambda: actions.append("R")
        app.copy_continuous_object_to_next = lambda: actions.append("E")

        app.copy_current_frame_to_next()
        app.next_rectangle()

        self.assertEqual(actions, ["R", "E"])

    def test_task_track_ids_are_unique_sorted_and_include_all_frames(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        first = Path("frame_001.jpg")
        second = Path("frame_002.jpg")
        app.images = [first, second]
        app.documents = {
            first: {"shapes": [rectangle("bee", 0, 0, 10, 10, group_id="9")]},
            second: {
                "shapes": [
                    rectangle("bee", 0, 0, 10, 10, group_id=2),
                    rectangle("bee", 20, 0, 30, 10, group_id=9),
                ]
            },
        }

        self.assertEqual(app._task_track_ids(), [2, 9])

    def test_track_id_change_targets_follow_selected_time_direction(self):
        first = Path("frame_001.jpg")
        second = Path("frame_002.jpg")
        third = Path("frame_003.jpg")
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.images = [first, second, third]
        app.documents = {
            first: {"shapes": [rectangle("bee", 0, 0, 10, 10, group_id=7)]},
            second: {
                "shapes": [
                    rectangle("bee", 0, 0, 10, 10, group_id=8),
                    rectangle("bee", 20, 0, 30, 10, group_id=7),
                ]
            },
            third: {"shapes": [rectangle("bee", 0, 0, 10, 10, group_id=7)]},
        }
        app.current_image_index = 1
        app._current_image_path = lambda: second

        current = app._track_id_change_targets(7, 1, "current")
        before = app._track_id_change_targets(7, 1, "before")
        after = app._track_id_change_targets(7, 1, "after")

        self.assertEqual(current, [(1, second, 1)])
        self.assertEqual(before, [(0, first, 0), (1, second, 1)])
        self.assertEqual(after, [(1, second, 1), (2, third, 0)])

    def test_default_track_id_enter_applies_and_returns_focus_to_canvas(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.default_track_id_text = FakeVariable()
        app.default_track_id_text.set("391")
        app.default_track_id = None
        focused = []
        app.overview_canvas = type(
            "Canvas", (), {"focus_set": lambda _self: focused.append(True)}
        )()
        app.root = type(
            "Root", (), {"after_idle": lambda _self, callback: callback()}
        )()
        app.activate_default_track_id = lambda: setattr(app, "default_track_id", 391)
        selection_cleared = []
        widget = type(
            "Widget",
            (),
            {"selection_clear": lambda _self: selection_cleared.append(True)},
        )()
        event = type("Event", (), {"widget": widget})()

        result = app._activate_default_track_id_from_entry(event)

        self.assertEqual(result, "break")
        self.assertEqual(selection_cleared, [True])
        self.assertEqual(focused, [True])

    def test_next_new_track_id_uses_task_maximum_plus_one(self):
        self.assertEqual(BeeKeypointAnnotator._next_new_track_id([]), 1)
        self.assertEqual(BeeKeypointAnnotator._next_new_track_id([1, 4, 9]), 10)

    def test_continuous_mode_ignores_current_selection_for_new_object_id(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        frame = Path("frame_001.jpg")
        app.images = [frame]
        app.documents = {
            frame: {
                "shapes": [
                    rectangle("bee", 0, 0, 10, 10, group_id=1),
                    rectangle("bee", 20, 0, 30, 10, group_id=9),
                ]
            }
        }
        app.continuous_annotation_mode = False
        app.default_track_id = 1
        app.default_track_id_explicit = False
        app.default_track_id_text = FakeVariable()
        app.status_var = FakeVariable()
        app._current_group_id = lambda: 1
        app._close_track_completion_dialog = lambda: None
        app._refresh_track_mode_ui = lambda: None
        app._set_image_index = lambda *_args, **_kwargs: None
        app._align_continuous_view_to_current_frame = lambda: None

        app.start_continuous_annotation_mode()

        self.assertEqual(app.continuous_active_track_id, 10)
        self.assertEqual(app.default_track_id_text.value, "10")

    def test_continuous_mode_respects_explicit_manual_id(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        frame = Path("frame_001.jpg")
        app.images = [frame]
        app.documents = {
            frame: {"shapes": [rectangle("bee", 0, 0, 10, 10, group_id=9)]}
        }
        app.continuous_annotation_mode = False
        app.default_track_id = 4
        app.default_track_id_explicit = True
        app.default_track_id_text = FakeVariable()
        app.status_var = FakeVariable()
        app._close_track_completion_dialog = lambda: None
        app._refresh_track_mode_ui = lambda: None
        app._set_image_index = lambda *_args, **_kwargs: None
        app._align_continuous_view_to_current_frame = lambda: None

        app.start_continuous_annotation_mode()

        self.assertEqual(app.continuous_active_track_id, 4)

    def test_continuous_mode_can_switch_back_to_selected_default_id(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.track_id_mode = False
        app.direction_review_mode = False
        app.continuous_annotation_mode = True
        app.continuous_active_track_id = 408
        app.continuous_draw_box_mode = True
        app.continuous_zoom_factor = 5.0
        app.continuous_focus_point = (321.0, 456.0)
        app.default_track_id = 408
        app.default_track_id_explicit = False
        app.default_track_id_text = FakeVariable()
        app.default_track_id_text.set("406")
        app.status_var = FakeVariable()
        app.rectangle_creation = object()
        app._refresh_default_track_id_controls = lambda: None
        image_indices = []
        app._set_image_index = lambda index, **_kwargs: image_indices.append(index)

        app.activate_default_track_id()

        self.assertEqual(app.continuous_active_track_id, 406)
        self.assertEqual(app.default_track_id, 406)
        self.assertTrue(app.default_track_id_explicit)
        self.assertFalse(app.continuous_draw_box_mode)
        self.assertEqual(app.continuous_zoom_factor, 5.0)
        self.assertEqual(app.continuous_focus_point, (321.0, 456.0))
        self.assertIsNone(app.rectangle_creation)
        self.assertEqual(image_indices, [0])
        self.assertIn("已切换到连续补标 ID 406", app.status_var.value)

    def test_v_advances_to_new_unused_id_and_restarts_first_frame(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        frame = Path("frame_001.jpg")
        app.images = [frame]
        app.documents = {
            frame: {"shapes": [rectangle("bee", 0, 0, 10, 10, group_id=405)]}
        }
        app.continuous_annotation_mode = True
        app.continuous_active_track_id = 405
        app.continuous_focus_point = (123.0, 234.0)
        app.continuous_zoom_factor = 4.5
        app.default_track_id = 405
        app.default_track_id_explicit = True
        app.default_track_id_text = FakeVariable()
        app.status_var = FakeVariable()
        app._close_track_completion_dialog = lambda: None
        app._refresh_track_mode_ui = lambda: None
        image_indices = []
        app._set_image_index = lambda index, **_kwargs: image_indices.append(index)
        app._align_continuous_view_to_current_frame = lambda: None

        app.start_continuous_annotation_mode()

        self.assertEqual(app.continuous_active_track_id, 406)
        self.assertEqual(app.default_track_id, 406)
        self.assertFalse(app.default_track_id_explicit)
        self.assertEqual(app.default_track_id_text.value, "406")
        self.assertEqual(image_indices, [0])
        self.assertEqual(app.continuous_focus_point, (123.0, 234.0))
        self.assertEqual(app.continuous_zoom_factor, 4.5)
        self.assertIn("已切换到 ID 406", app.status_var.value)

    def test_h_hides_all_boxes_including_current_and_keeps_keypoints(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.show_other_boxes = FakeVariable()
        app.show_other_boxes.set(True)
        app.status_var = FakeVariable()
        refreshes = []
        app._refresh_all = lambda: refreshes.append(True)

        app.toggle_continuous_other_boxes()

        self.assertFalse(app.show_other_boxes.get())
        self.assertEqual(refreshes, [True])
        self.assertIn("全部检测框（含当前框）已隐藏", app.status_var.value)
        self.assertIn("关键点保持显示", app.status_var.value)

    def test_b_toggles_box_id_labels(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.show_track_ids = FakeVariable()
        app.show_track_ids.set(True)
        app.status_var = FakeVariable()
        refreshes = []
        app._refresh_all = lambda: refreshes.append(True)

        app.toggle_track_ids()

        self.assertFalse(app.show_track_ids.get())
        self.assertEqual(refreshes, [True])
        self.assertEqual(app.status_var.value, "检测框 ID 已隐藏")

    def test_continuous_mode_a_d_switch_adjacent_frames(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.continuous_annotation_mode = True
        app.continuous_active_track_id = 404
        app.images = [Path("1.jpg"), Path("2.jpg"), Path("3.jpg")]
        app.current_image_index = 1
        app.current_rectangle_index = 7
        app.continuous_focus_point = (123.0, 456.0)
        app.continuous_zoom_factor = 3.8
        app.detail_cache_key = "old"
        app.status_var = FakeVariable()
        calls = []
        app._set_image_index = lambda *args, **kwargs: calls.append((args, kwargs))
        app._continuous_rectangle_position = lambda: -1
        app._remember_current_rectangle = lambda: calls.append("remember")
        app._refresh_all = lambda: calls.append("refresh")

        app.next_track_frame()

        self.assertEqual(calls[0][0][0], 2)
        self.assertFalse(calls[0][1]["auto_confirm_viewed"])
        self.assertEqual(calls[1], "refresh")
        self.assertNotIn("remember", calls)
        self.assertEqual(app.continuous_focus_point, (123.0, 456.0))
        self.assertEqual(app.continuous_zoom_factor, 3.8)
        self.assertEqual(app.current_rectangle_index, -1)
        self.assertIsNone(app.detail_cache_key)
        self.assertIn("第 3/3 帧", app.status_var.value)

    def test_direction_review_q_e_w_routes_to_review_actions(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.direction_review_mode = True
        actions = []
        app.previous_direction_review_item = lambda: actions.append("previous")
        app.confirm_direction_review_and_next = lambda: actions.append("confirm_next")
        app.next_direction_review_item = lambda: actions.append("next")

        app.previous_rectangle()
        app.next_rectangle()
        app.next_track_id()

        self.assertEqual(actions, ["previous", "confirm_next", "next"])

    def test_legacy_image_shortcuts_are_migrated_to_track_navigation(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.settings = {
            "shortcuts": {
                "previous_image": ["A", "无"],
                "next_image": ["D", "无"],
                "previous_track_frame": ["J", "无"],
                "next_track_frame": ["K", "无"],
            }
        }
        shortcuts = app._load_shortcuts()
        self.assertEqual(shortcuts["previous_image"], ["Z", "无"])
        self.assertEqual(shortcuts["next_image"], ["C", "无"])
        self.assertEqual(shortcuts["previous_track_frame"], ["A", "无"])
        self.assertEqual(shortcuts["next_track_frame"], ["D", "无"])

    def test_legacy_frame_copy_shortcut_is_migrated_to_r(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.settings = {
            "shortcuts": {
                "copy_frame_to_next": ["P", "无"],
            }
        }
        shortcuts = app._load_shortcuts()
        self.assertEqual(shortcuts["copy_frame_to_next"], ["R", "无"])

    def test_legacy_clear_id_shortcut_is_released_for_box_id_toggle(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.settings = {
            "shortcuts": {
                "clear_default_track_id": ["V", "无"],
            }
        }
        shortcuts = app._load_shortcuts()
        self.assertEqual(shortcuts["start_continuous_annotation"], ["V", "无"])
        self.assertEqual(shortcuts["toggle_track_ids"], ["B", "无"])
        self.assertEqual(shortcuts["clear_default_track_id"], ["无", "无"])

    def test_default_shortcuts_have_no_conflicts(self):
        assigned = []
        for shortcuts in DEFAULT_SHORTCUTS.values():
            assigned.extend(shortcut for shortcut in shortcuts if shortcut != "无")
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_keyboard_shortcut_conversion(self):
        convert = BeeKeypointAnnotator._shortcut_to_tk_sequence
        self.assertEqual(convert("Ctrl+S"), "<Control-s>")
        self.assertEqual(convert("Shift+Delete"), "<Shift-Delete>")
        self.assertEqual(convert("A"), "<a>")
        self.assertEqual(convert("F5"), "<F5>")
        self.assertEqual(convert("PageUp"), "<Prior>")
        self.assertIsNone(convert("鼠标侧键1"))
        self.assertIsNone(convert("无"))

    def test_ime_process_key_is_restored_to_physical_letter(self):
        resolve = BeeKeypointAnnotator._physical_virtual_key
        self.assertEqual(resolve(229, ord("G")), ord("G"))
        self.assertEqual(resolve(229, 0, [ord("Q")]), ord("Q"))

    def test_normal_key_resolution_does_not_enter_windows_ime_api(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        with patch.object(
            app,
            "_windows_pressed",
            side_effect=AssertionError("普通字母不应扫描原生键盘状态"),
        ):
            self.assertEqual(app._resolve_windows_virtual_key(1, ord("G")), ord("G"))

    def test_letter_shortcuts_ignore_case_shift_after_exact_match(self):
        candidates = BeeKeypointAnnotator._shortcut_candidates_for_virtual_key
        self.assertEqual(candidates(ord("A"), ()), ["A"])
        self.assertEqual(candidates(ord("A"), ("Shift",)), ["Shift+A", "A"])
        self.assertEqual(
            candidates(ord("P"), ("Ctrl", "Shift")),
            ["Ctrl+Shift+P", "Ctrl+P"],
        )

    def test_caps_lock_state_is_not_treated_as_shortcut_modifier(self):
        modifiers = BeeKeypointAnnotator._tk_state_modifiers
        self.assertEqual(modifiers(0x0002), ())
        self.assertEqual(modifiers(0x0002 | 0x0004), ("Ctrl",))

    def test_virtual_key_names_cover_runtime_shortcut_keys(self):
        key_name = BeeKeypointAnnotator._virtual_key_name
        self.assertEqual(key_name(ord("Q")), "Q")
        self.assertEqual(key_name(0x20), "Space")
        self.assertEqual(key_name(0x2E), "Delete")
        self.assertEqual(key_name(0x70), "F1")

    def test_shortcuts_convert_back_to_windows_virtual_keys(self):
        convert = BeeKeypointAnnotator._shortcut_virtual_key
        self.assertEqual(convert("Q"), ord("Q"))
        self.assertEqual(convert("Ctrl+P"), ord("P"))
        self.assertEqual(convert("F1"), 0x70)
        self.assertEqual(convert("Space"), 0x20)
        self.assertIsNone(convert("鼠标侧键1"))
        self.assertIsNone(convert("无"))

    def test_keyboard_poll_dispatches_only_on_key_down_edge(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.shortcuts = {"previous_rectangle": ["Q"]}
        app.pressed_shortcut_keys = set()
        app._main_window_is_foreground = lambda: True
        app._main_window_has_focus = lambda: True
        app._windows_modifier_names = lambda: ()
        executed = []
        app._execute_shortcut_action = executed.append

        with patch.object(app, "_windows_async_pressed", return_value=True):
            self.assertEqual(app._poll_keyboard_shortcuts_once(), "break")
            self.assertIsNone(app._poll_keyboard_shortcuts_once())
        self.assertEqual(executed, ["previous_rectangle"])

        with patch.object(app, "_windows_async_pressed", return_value=False):
            self.assertIsNone(app._poll_keyboard_shortcuts_once())
        with patch.object(app, "_windows_async_pressed", return_value=True):
            self.assertEqual(app._poll_keyboard_shortcuts_once(), "break")
        self.assertEqual(executed, ["previous_rectangle", "previous_rectangle"])

    def test_keyboard_hook_queue_dispatches_physical_key(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.shortcuts = {"next_rectangle": ["E"]}
        app.keyboard_event_queue = queue.SimpleQueue()
        app.keyboard_event_queue.put((ord("E"), ()))
        app._main_window_is_foreground = lambda: True
        app._main_window_has_focus = lambda: True
        executed = []
        app._execute_shortcut_action = executed.append

        self.assertEqual(app._drain_keyboard_hook_events(), "break")
        self.assertEqual(executed, ["next_rectangle"])

    def test_main_shortcuts_pause_while_text_input_has_focus(self):
        class FakeWidget:
            def __init__(self, toplevel, widget_class):
                self.toplevel = toplevel
                self.widget_class = widget_class

            def winfo_toplevel(self):
                return self.toplevel

            def winfo_class(self):
                return self.widget_class

        class FakeRoot:
            focused = None

            def focus_get(self):
                return self.focused

        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.root = FakeRoot()
        app.root.focused = FakeWidget(app.root, "Canvas")
        self.assertTrue(app._main_window_has_focus())
        app.root.focused = FakeWidget(app.root, "TEntry")
        self.assertFalse(app._main_window_has_focus())
        app.root.focused = FakeWidget(object(), "Canvas")
        self.assertFalse(app._main_window_has_focus())

    def test_auto_review_can_be_undone_after_switching_images(self):
        first = Path("first.jpg")
        second = Path("second.jpg")
        first_document = {
            "shapes": [
                {
                    "label": "bee",
                    "points": [[0, 0], [10, 0], [10, 20], [0, 20]],
                    "group_id": 7,
                    "shape_type": "rectangle",
                    "flags": {REVIEW_REQUIRED_FLAG: True},
                }
            ]
        }
        add_or_replace_point(first_document, 0, "head", (2, 4), suggested=True)
        add_or_replace_point(first_document, 0, "tail", (8, 16), suggested=True)

        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.images = [first, second]
        app.documents = {first: first_document, second: {"shapes": []}}
        app.current_image_index = 0
        app.current_rectangle_index = 0
        app.dirty_images = set()
        app.undo_stacks = {}
        app.undo_order = []
        app.selected_rectangle_by_image = {}
        app.status_var = FakeVariable()
        app.save_current = lambda: app.dirty_images.discard(app._current_image_path())
        app.save_all = lambda: app.dirty_images.clear()
        app._refresh_all = lambda: None

        def set_image(index, preferred_rectangle_position=None, **_kwargs):
            app.current_image_index = index
            app.current_rectangle_index = preferred_rectangle_position or 0

        app._set_image_index = set_image

        self.assertEqual(app._auto_confirm_viewed_rectangle(), 2)
        self.assertFalse(rectangle_review_is_pending(first_document, 0))
        app.current_image_index = 1
        app.undo()
        self.assertEqual(app.current_image_index, 0)
        self.assertTrue(rectangle_review_is_pending(first_document, 0))

    def test_clicked_field_captures_keyboard_shortcut(self):
        capture = BeeKeypointAnnotator._captured_shortcut_name
        self.assertEqual(capture("q", 0), "Q")
        self.assertEqual(capture("r", 0x0004), "Ctrl+R")
        self.assertEqual(capture("F3", 0), "F3")
        self.assertEqual(capture("F3", 0x20000), "F3")
        self.assertEqual(capture("F3", 0x0008), "Alt+F3")
        self.assertEqual(capture("Delete", 0x0001), "Shift+Delete")
        self.assertEqual(capture("Prior", 0), "PageUp")
        self.assertIsNone(capture("Control_L", 0x0004))

    def test_capture_uses_only_modifiers_actually_pressed(self):
        capture = BeeKeypointAnnotator._captured_shortcut_with_modifiers
        self.assertEqual(capture("i", set()), "I")
        self.assertEqual(capture("i", {"Ctrl"}), "Ctrl+I")
        self.assertEqual(
            capture("i", {"Alt", "Shift", "Ctrl"}),
            "Ctrl+Shift+Alt+I",
        )
        self.assertEqual(
            BeeKeypointAnnotator._modifier_name("Control_L"),
            "Ctrl",
        )

    @staticmethod
    def _rectangle_document():
        return {
            "shapes": [
                {
                    "label": "bee",
                    "points": [[0, 0], [10, 0], [10, 20], [0, 20]],
                    "group_id": 7,
                    "shape_type": "rectangle",
                    "flags": {},
                }
            ]
        }

    def test_frame_table_status_reports_missing_tail(self):
        document = self._rectangle_document()
        add_or_replace_point(document, 0, "head", (2, 4))
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        status, detail, complete = app._rectangle_table_status(
            rectangle_records(document)[0],
            keypoints_for_rectangle(document, 0),
        )
        self.assertEqual(status, "不完整")
        self.assertIn("缺 tail", detail)
        self.assertFalse(complete)

    def test_frame_completion_requires_complete_manual_pair(self):
        document = self._rectangle_document()
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        self.assertFalse(app._frame_is_complete(document))
        add_or_replace_point(document, 0, "head", (2, 4))
        self.assertFalse(app._frame_is_complete(document))
        add_or_replace_point(document, 0, "tail", (8, 16))
        self.assertTrue(app._frame_is_complete(document))

    def test_frame_completion_transition_shows_one_notification(self):
        document = self._rectangle_document()
        add_or_replace_point(document, 0, "head", (2, 4))
        add_or_replace_point(document, 0, "tail", (8, 16))
        image_path = Path("complete.jpg")

        class ImmediateRoot:
            @staticmethod
            def after_idle(callback):
                callback()

        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.root = ImmediateRoot()
        app.images = [image_path]
        app.current_image_index = 0
        app.documents = {image_path: document}
        app.frame_completion_state = {image_path: False}
        with patch("app.messagebox.showinfo") as showinfo:
            self.assertTrue(
                app._update_frame_completion_state(image_path, notify=True)
            )
            app._update_frame_completion_state(image_path, notify=True)
        showinfo.assert_called_once()

    def test_continuous_mode_suppresses_frame_completion_notification(self):
        document = self._rectangle_document()
        add_or_replace_point(document, 0, "head", (2, 4))
        add_or_replace_point(document, 0, "tail", (8, 16))
        image_path = Path("complete.jpg")

        class ImmediateRoot:
            @staticmethod
            def after_idle(callback):
                callback()

        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.root = ImmediateRoot()
        app.continuous_annotation_mode = True
        app.images = [image_path]
        app.current_image_index = 0
        app.documents = {image_path: document}
        app.frame_completion_state = {image_path: False}

        with patch("app.messagebox.showinfo") as showinfo:
            self.assertTrue(
                app._update_frame_completion_state(image_path, notify=True)
            )

        showinfo.assert_not_called()
        self.assertTrue(app.frame_completion_state[image_path])

    @staticmethod
    def _track_document(group_id, rect=(0, 0, 10, 20)):
        x1, y1, x2, y2 = rect
        return {
            "shapes": [
                {
                    "label": "bee",
                    "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                    "group_id": group_id,
                    "shape_type": "rectangle",
                    "flags": {},
                }
            ]
        }

    def test_track_id_rows_are_sorted_and_count_remaining_occurrences(self):
        first = Path("first.jpg")
        second = Path("second.jpg")
        first_document = self._track_document(10)
        second_document = self._track_document(2)
        add_or_replace_point(second_document, 0, "head", (2, 4))
        add_or_replace_point(second_document, 0, "tail", (8, 16))

        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.images = [first, second]
        app.documents = {first: first_document, second: second_document}
        rows = app._track_id_rows()

        self.assertEqual([row["group_id"] for row in rows], [2, 10])
        self.assertEqual(rows[0]["completed"], 1)
        self.assertEqual(rows[0]["remaining"], 0)
        self.assertEqual(rows[1]["completed"], 0)
        self.assertEqual(rows[1]["remaining"], 1)

    def test_id_mode_e_confirms_copies_and_moves_to_next_occurrence(self):
        first = Path("first.jpg")
        second = Path("second.jpg")
        first_document = self._track_document(7, (0, 0, 10, 20))
        second_document = self._track_document(7, (100, 100, 120, 140))
        add_or_replace_point(first_document, 0, "head", (2, 4))
        add_or_replace_point(first_document, 0, "tail", (8, 16))

        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.track_id_mode = True
        app.track_mode_current_id = 7
        app.images = [first, second]
        app.documents = {first: first_document, second: second_document}
        app.current_image_index = 0
        app.current_rectangle_index = 0
        app.dirty_images = set()
        app.undo_stacks = {}
        app.undo_order = []
        app.selected_rectangle_by_image = {}
        app.overview_dirty_positions = {}
        app.status_var = FakeVariable()
        app.save_current = lambda: app.dirty_images.discard(app._current_image_path())
        app.save_all = lambda: app.dirty_images.clear()
        app._refresh_all = lambda: None

        def set_image(index, preferred_group_id=None, **_kwargs):
            app.current_image_index = index
            matches = [
                position
                for position, rectangle in enumerate(app._current_rectangles())
                if rectangle.get("group_id") == preferred_group_id
            ]
            app.current_rectangle_index = matches[0]

        app._set_image_index = set_image
        completed_dialogs = []
        app._show_track_completion_dialog = completed_dialogs.append

        app.confirm_and_advance_track()
        self.assertEqual(app.current_image_index, 1)
        target_records = keypoints_for_rectangle(second_document, 0)
        self.assertEqual(len(target_records), 2)
        self.assertTrue(all(point_is_suggested(record) for record in target_records))

        app.confirm_and_advance_track()
        self.assertFalse(
            any(
                point_is_suggested(record)
                for record in keypoints_for_rectangle(second_document, 0)
            )
        )
        self.assertEqual(completed_dialogs, [7])

    def test_id_mode_e_clears_stale_rectangle_pending_flag(self):
        image_path = Path("stale.jpg")
        document = self._track_document(7)
        document["shapes"][0].setdefault("flags", {})[
            REVIEW_REQUIRED_FLAG
        ] = True
        add_or_replace_point(document, 0, "head", (2, 4))
        add_or_replace_point(document, 0, "tail", (8, 16))

        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.images = [image_path]
        app.documents = {image_path: document}
        app.current_image_index = 0
        app.current_rectangle_index = 0
        app.dirty_images = set()
        app.undo_stacks = {}
        app.undo_order = []
        app.selected_rectangle_by_image = {}
        app.overview_dirty_positions = {}
        app.status_var = FakeVariable()
        app.save_current = lambda: app.dirty_images.discard(image_path)

        self.assertTrue(rectangle_review_is_pending(document, 0))
        self.assertTrue(app._confirm_current_track_pair())
        self.assertFalse(rectangle_review_is_pending(document, 0))

    def test_legacy_manual_pair_pending_flags_are_repaired_on_load(self):
        manual_path = Path("manual.jpg")
        suggested_path = Path("suggested.jpg")
        manual_document = self._track_document(7)
        suggested_document = self._track_document(8)
        for document in (manual_document, suggested_document):
            document["shapes"][0].setdefault("flags", {})[
                REVIEW_REQUIRED_FLAG
            ] = True
        add_or_replace_point(manual_document, 0, "head", (2, 4))
        add_or_replace_point(manual_document, 0, "tail", (8, 16))
        add_or_replace_point(suggested_document, 0, "head", (2, 4), suggested=True)
        add_or_replace_point(suggested_document, 0, "tail", (8, 16), suggested=True)

        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.images = [manual_path, suggested_path]
        app.documents = {
            manual_path: manual_document,
            suggested_path: suggested_document,
        }
        app.dirty_images = set()
        app.save_all = lambda: None

        self.assertEqual(app._repair_legacy_manual_review_flags(), 1)
        self.assertFalse(rectangle_review_is_pending(manual_document, 0))
        self.assertTrue(rectangle_review_is_pending(suggested_document, 0))

    def test_next_unfinished_track_wraps_and_skips_current_id(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app._track_id_rows = lambda: [
            {"group_id": 1, "remaining": 2},
            {"group_id": 2, "remaining": 0},
            {"group_id": 3, "remaining": 0},
        ]

        self.assertEqual(app._next_unfinished_track_id(3), 1)
        self.assertIsNone(app._next_unfinished_track_id(1))

    def test_w_moves_to_next_track_id_first_unfinished_occurrence(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.track_id_mode = True
        app.track_mode_current_id = 2
        app.status_var = FakeVariable()
        app._track_id_rows = lambda: [
            {"group_id": 1},
            {"group_id": 2},
            {"group_id": 3},
        ]
        app._close_track_completion_dialog = lambda: None
        targets = []
        app._go_to_track_id = lambda group_id, first_unfinished: targets.append(
            (group_id, first_unfinished)
        )

        app.next_track_id()

        self.assertEqual(targets, [(3, True)])

    def test_moved_rectangle_remaps_only_its_keypoints(self):
        document = self._track_document(7, (0, 0, 10, 20))
        document["shapes"].append(
            {
                "label": "other",
                "points": [[50, 60]],
                "group_id": 999,
                "shape_type": "point",
                "flags": {},
            }
        )
        add_or_replace_point(document, 0, "head", (2, 4))
        add_or_replace_point(document, 0, "tail", (8, 16))
        snapshot = BeeKeypointAnnotator._snapshot_drag_keypoints(
            document,
            0,
            (0, 0, 10, 20),
        )

        changed = BeeKeypointAnnotator._apply_drag_keypoints(
            document,
            snapshot,
            (100, 200, 110, 220),
        )

        self.assertEqual(changed, 2)
        points = {
            record["label"]: record["point"]
            for record in keypoints_for_rectangle(document, 0)
        }
        self.assertEqual(points["head"], (102.0, 204.0))
        self.assertEqual(points["tail"], (108.0, 216.0))
        other = next(
            shape for shape in document["shapes"] if shape.get("label") == "other"
        )
        self.assertEqual(other["points"], [[50, 60]])

    def test_resized_rectangle_keeps_keypoints_at_absolute_positions(self):
        image_path = Path("frame.jpg")
        document = self._track_document(7, (0, 0, 10, 20))
        add_or_replace_point(document, 0, "head", (2, 4))
        add_or_replace_point(document, 0, "tail", (8, 16))
        snapshot = BeeKeypointAnnotator._snapshot_drag_keypoints(
            document,
            0,
            (0, 0, 10, 20),
        )
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.rectangle_drag = {
            "view": "detail",
            "image_path": image_path,
            "rectangle_position": 0,
            "original_rect": (0, 0, 10, 20),
            "original_keypoints": snapshot,
            "start": (10, 20),
            "transform": (1, 0, 0),
            "image_size": (100, 100),
            "mode": "se",
            "changed": False,
            "last_redraw": 0.0,
        }
        app._current_image_path = lambda: image_path
        app._current_document = lambda: document
        app._refresh_all = lambda: None

        app._continue_rectangle_drag_at(20, 40, "detail")

        self.assertEqual(rectangle_records(document)[0]["rect"], (0, 0, 20, 40))
        points = {
            record["label"]: record["point"]
            for record in keypoints_for_rectangle(document, 0)
        }
        self.assertEqual(points["head"], (2.0, 4.0))
        self.assertEqual(points["tail"], (8.0, 16.0))

    def test_enter_id_mode_skips_completed_current_id(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.track_id_mode = False
        app.track_mode_current_id = None
        app._track_id_rows = lambda: [
            {"group_id": 1, "remaining": 0},
            {"group_id": 2, "remaining": 3},
        ]
        app._current_group_id = lambda: 1
        app._refresh_track_mode_ui = lambda: None
        targets = []
        app._go_to_track_id = lambda group_id, first_unfinished: targets.append(
            (group_id, first_unfinished)
        )

        app.toggle_track_id_mode()

        self.assertTrue(app.track_id_mode)
        self.assertEqual(targets, [(2, False)])

    def test_id_mode_a_d_navigation_does_not_auto_confirm(self):
        first = Path("first.jpg")
        second = Path("second.jpg")
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.track_id_mode = True
        app.track_mode_current_id = 7
        app.images = [first, second]
        app.documents = {
            first: self._track_document(7),
            second: self._track_document(7),
        }
        app.current_image_index = 0
        app.current_rectangle_index = 0
        app.status_var = FakeVariable()
        calls = []
        app._set_image_index = lambda *args, **kwargs: calls.append((args, kwargs))

        app.next_track_frame()

        self.assertEqual(calls[0][0][0], 1)
        self.assertFalse(calls[0][1]["auto_confirm_viewed"])

    def test_completion_dialog_keyboard_hook_routes_e_and_q(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.keyboard_event_queue = queue.SimpleQueue()
        app.keyboard_event_queue.put((ord("E"), ()))
        app.keyboard_event_queue.put((ord("Q"), ()))
        app._main_window_is_foreground = lambda: True
        app._track_completion_dialog_exists = lambda: True
        actions = []
        app._accept_track_completion = lambda: actions.append("E")
        app._return_previous_track_from_completion = lambda: actions.append("Q")

        self.assertEqual(app._drain_keyboard_hook_events(), "break")
        self.assertEqual(actions, ["E", "Q"])


@unittest.skipIf(win32gui is None, "仅在 Windows 上测试原生侧键")
class ShortcutDialogIntegrationTests(unittest.TestCase):
    @staticmethod
    def _all_widgets(widget):
        widgets = []
        for child in widget.winfo_children():
            widgets.append(child)
            widgets.extend(ShortcutDialogIntegrationTests._all_widgets(child))
        return widgets

    def test_dialog_captures_plain_key_modifier_and_native_side_button(self):
        root = tk.Tk()
        root.geometry("1x1+0+0")
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.root = root
        app.shortcuts = copy.deepcopy(DEFAULT_SHORTCUTS)

        try:
            app.open_shortcut_manager()
            root.update()
            time.sleep(0.15)
            root.update()
            dialog = next(
                widget
                for widget in root.winfo_children()
                if isinstance(widget, tk.Toplevel)
            )
            buttons = [
                widget
                for widget in self._all_widgets(dialog)
                if widget.winfo_class() == "TButton"
            ]
            capture_button = next(
                button for button in buttons if button.cget("text") == "Q"
            )

            capture_button.invoke()
            dialog.focus_force()
            capture_button.event_generate("<KeyPress-i>", state=0x0008)
            root.update()
            self.assertEqual(capture_button.cget("text"), "I")

            capture_button.invoke()
            capture_button.event_generate("<KeyPress-Control_L>")
            capture_button.event_generate("<KeyPress-i>")
            root.update()
            self.assertEqual(capture_button.cget("text"), "Ctrl+I")

            capture_button.invoke()
            dialog_handle = app._tk_toplevel_window_handle(dialog)
            self.assertEqual(
                win32gui.GetWindowText(dialog_handle),
                "快捷键设置",
            )
            win32gui.SendMessage(dialog_handle, 0x020B, 1 << 16, 0)
            root.update()
            self.assertEqual(capture_button.cget("text"), "鼠标侧键1")

            next(
                button for button in buttons if button.cget("text") == "取消"
            ).invoke()
            root.update()
        finally:
            if root.winfo_exists():
                root.destroy()

    def test_native_window_hook_passes_keyboard_messages_to_tk(self):
        app = BeeKeypointAnnotator.__new__(BeeKeypointAnnotator)
        app.native_window_hooks = {123: 456}
        with patch("app.win32gui.CallWindowProc", return_value=99) as call_window:
            result = app._native_window_message(123, 0x0100, ord("G"), 0)
        self.assertEqual(result, 99)
        call_window.assert_called_once_with(456, 123, 0x0100, ord("G"), 0)


if __name__ == "__main__":
    unittest.main()
