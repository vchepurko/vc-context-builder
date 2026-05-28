"""Tests for ``orm_field_usage.find_usage`` — AST-precise replacement
for ``grep -rn <column>`` when scoping ORM refactors.

Builds a tiny Python project tree and asserts that each access pattern
(class form, instance form, write target, ignored migration, false
positives) is classified correctly.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from indexers.orm_field_usage import find_usage


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class TestBasics(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="orm_usage_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_class_form_read_match(self) -> None:
        _write(
            os.path.join(self.root, "a.py"),
            "from models import Product\ndef get_photo():\n    return Product.photo_file_id\n",
        )
        hits = find_usage(self.root, "Product", "photo_file_id")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "a.py")
        self.assertEqual(hits[0]["kind"], "read")
        self.assertEqual(hits[0]["line"], 3)
        self.assertIn("photo_file_id", hits[0]["context"])

    def test_instance_form_read_match(self) -> None:
        _write(
            os.path.join(self.root, "b.py"),
            "def show(product):\n    name = product.photo_file_id\n    return name\n",
        )
        hits = find_usage(self.root, "Product", "photo_file_id")
        kinds = {h["kind"] for h in hits}
        self.assertEqual(kinds, {"read"})
        self.assertEqual(hits[0]["line"], 2)

    def test_write_classified_correctly(self) -> None:
        _write(
            os.path.join(self.root, "c.py"),
            "def assign(product, fid):\n    product.photo_file_id = fid\n",
        )
        hits = find_usage(self.root, "Product", "photo_file_id")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "write")

    def test_unrelated_attribute_not_matched(self) -> None:
        # ``user.photo_file_id`` — receiver name doesn't match Product
        # or product, so it's correctly skipped (avoids over-broad grep).
        _write(
            os.path.join(self.root, "d.py"),
            "def read_user(user):\n    return user.photo_file_id\n",
        )
        hits = find_usage(self.root, "Product", "photo_file_id")
        self.assertEqual(hits, [])

    def test_string_in_comment_not_matched(self) -> None:
        # Comments and strings containing 'photo_file_id' must be ignored.
        _write(
            os.path.join(self.root, "e.py"),
            "# TODO: rename photo_file_id\nMSG = 'set photo_file_id on the row'\n",
        )
        hits = find_usage(self.root, "Product", "photo_file_id")
        self.assertEqual(hits, [])

    def test_alembic_directory_skipped(self) -> None:
        # Migrations call op.add_column('products', sa.Column('photo_file_id'))
        # — pure strings, and they overwhelm any signal. We skip the
        # whole alembic/ tree by convention.
        _write(
            os.path.join(self.root, "alembic/versions/0001.py"),
            "from sqlalchemy import Column\n"
            "Product = type('Product', (), {})\n"
            "def upgrade():\n"
            "    Product.photo_file_id = 'in-migration'\n",
        )
        hits = find_usage(self.root, "Product", "photo_file_id")
        self.assertEqual(hits, [])

    def test_other_models_not_matched(self) -> None:
        _write(
            os.path.join(self.root, "f.py"),
            "def x(order):\n    return order.photo_file_id\n",
        )
        hits_product = find_usage(self.root, "Product", "photo_file_id")
        hits_order = find_usage(self.root, "Order", "photo_file_id")
        self.assertEqual(hits_product, [])
        self.assertEqual(len(hits_order), 1)
        self.assertEqual(hits_order[0]["kind"], "read")

    def test_empty_inputs_return_empty(self) -> None:
        self.assertEqual(find_usage(self.root, "", "x"), [])
        self.assertEqual(find_usage(self.root, "Model", ""), [])

    def test_limit_short_circuits(self) -> None:
        _write(
            os.path.join(self.root, "g.py"),
            "\n".join(f"x{i} = Product.photo_file_id" for i in range(10)) + "\n",
        )
        hits = find_usage(self.root, "Product", "photo_file_id", limit=3)
        self.assertEqual(len(hits), 3)

    def test_syntax_error_file_skipped(self) -> None:
        _write(os.path.join(self.root, "good.py"), "Product.photo_file_id\n")
        _write(os.path.join(self.root, "bad.py"), "def broken(:\n  pass\n")
        hits = find_usage(self.root, "Product", "photo_file_id")
        # `bad.py` skipped silently, `good.py` still produces a hit.
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "good.py")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
