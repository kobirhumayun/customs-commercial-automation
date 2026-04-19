from __future__ import annotations

import unittest

from project.utils.hashing import canonical_json_hash


class HashingTests(unittest.TestCase):
    def test_canonical_json_hash_ignores_dict_key_order(self) -> None:
        first = {"b": 2, "a": {"d": 4, "c": 3}}
        second = {"a": {"c": 3, "d": 4}, "b": 2}

        self.assertEqual(canonical_json_hash(first), canonical_json_hash(second))


if __name__ == "__main__":
    unittest.main()
