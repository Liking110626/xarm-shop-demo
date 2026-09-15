import unittest

from xarm_grasp.receipt import (
    extract_ocr_text,
    match_receipt_products,
    normalize_receipt_text,
    select_receipt_product,
)


CONFIG = {
    "products": {
        "可口可乐罐装": {"aliases": ["可口可乐", "可乐", "coke"], "class_id": 0},
        "雪碧罐装": {"aliases": ["雪碧", "sprite"], "class_id": 1},
        "芬达罐装": {"aliases": ["芬达", "fanta"], "class_id": 2},
    }
}


class ReceiptTests(unittest.TestCase):
    def test_normalize_and_extract_markdown(self):
        self.assertEqual(normalize_receipt_text(" 可 乐：1\nCOKE "), "可乐1coke")
        self.assertEqual(extract_ocr_text({"md_results": [{"text": "雪碧"}, "芬达"]}),
                         "雪碧\n\n芬达")

    def test_alias_maps_to_canonical_product(self):
        selected = select_receipt_product("商品名称：可乐；数量：1", CONFIG)
        self.assertEqual(selected["name"], "可口可乐罐装")
        self.assertEqual(selected["class_id"], 0)

    def test_multiple_aliases_of_same_product_are_one_match(self):
        matches = match_receipt_products("可口可乐罐装 / 可乐", CONFIG)
        self.assertEqual([item["name"] for item in matches], ["可口可乐罐装"])

    def test_multiple_product_types_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "multiple configured product types"):
            select_receipt_product("可乐 芬达", CONFIG)

    def test_unknown_product_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not contain"):
            select_receipt_product("矿泉水一瓶", CONFIG)

    def test_shared_alias_configuration_is_rejected(self):
        config = {"products": {
            "A": {"aliases": ["相同"], "class_id": 0},
            "B": {"aliases": ["相同"], "class_id": 1},
        }}
        with self.assertRaisesRegex(ValueError, "is shared"):
            match_receipt_products("相同", config)


if __name__ == "__main__":
    unittest.main()
