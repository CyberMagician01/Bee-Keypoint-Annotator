import unittest

from PIL import Image

from app import ASSISTANT_IMAGE_PATH


class VisualAssetTests(unittest.TestCase):
    def test_assistant_asset_has_transparent_background(self):
        self.assertTrue(ASSISTANT_IMAGE_PATH.exists())
        with Image.open(ASSISTANT_IMAGE_PATH) as image:
            rgba = image.convert("RGBA")
            alpha = rgba.getchannel("A")
            corners = [
                alpha.getpixel((0, 0)),
                alpha.getpixel((rgba.width - 1, 0)),
                alpha.getpixel((0, rgba.height - 1)),
                alpha.getpixel((rgba.width - 1, rgba.height - 1)),
            ]
            self.assertEqual(corners, [0, 0, 0, 0])
            self.assertIsNotNone(alpha.getbbox())


if __name__ == "__main__":
    unittest.main()
