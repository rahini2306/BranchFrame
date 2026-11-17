import unittest
from branchframe.domain import validate_timeline, merge, merge_base, diff, digest

A = "a" * 32


def clip(cid="one", **updates):
    return dict(
        {"id": cid, "asset": A, "start": 0, "end": 10, "caption": "", "muted": False},
        **updates,
    )


def tl(*clips):
    return {"clips": list(clips)}


class TimelineTests(unittest.TestCase):
    def test_valid_is_copied(self):
        value = tl(clip())
        actual = validate_timeline(value)
        actual["clips"][0]["end"] = 4
        self.assertEqual(value["clips"][0]["end"], 10)

    def test_invalid_ranges(self):
        for start, end in [
            (4, 3),
            (-1, 3),
            (0, float("nan")),
            (0, float("inf")),
            (False, 3),
        ]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                validate_timeline(tl(clip(start=start, end=end)))

    def test_duplicate_identity_rejected(self):
        with self.assertRaises(ValueError):
            validate_timeline(tl(clip(), clip()))

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            validate_timeline(tl(clip(command="bad")))

    def test_unchanged_merge(self):
        base = tl(clip())
        self.assertEqual(merge(base, base, base), {"timeline": base, "conflicts": []})

    def test_independent_fields_merge(self):
        result = merge(tl(clip()), tl(clip(caption="Hello")), tl(clip(muted=True)))
        self.assertFalse(result["conflicts"])
        self.assertEqual(result["timeline"], tl(clip(caption="Hello", muted=True)))

    def test_conflicting_caption_requires_resolution(self):
        result = merge(tl(clip()), tl(clip(caption="A")), tl(clip(caption="B")))
        self.assertEqual(result["conflicts"][0]["path"], "clips/one/caption")
        result = merge(
            tl(clip()),
            tl(clip(caption="A")),
            tl(clip(caption="B")),
            {"clips/one/caption": "theirs"},
        )
        self.assertFalse(result["conflicts"])
        self.assertEqual(result["timeline"]["clips"][0]["caption"], "B")

    def test_delete_vs_unchanged(self):
        self.assertEqual(merge(tl(clip()), tl(), tl(clip()))["timeline"], tl())

    def test_delete_vs_modify(self):
        result = merge(tl(clip()), tl(), tl(clip(caption="changed")))
        self.assertEqual(result["conflicts"][0]["path"], "clips/one")
        result = merge(
            tl(clip()), tl(), tl(clip(caption="changed")), {"clips/one": "theirs"}
        )
        self.assertEqual(result["timeline"], tl(clip(caption="changed")))

    def test_independent_insertions(self):
        result = merge(tl(), tl(clip("a")), tl(clip("b")))
        self.assertEqual({c["id"] for c in result["timeline"]["clips"]}, {"a", "b"})

    def test_one_sided_order(self):
        a, b, c = clip("a"), clip("b"), clip("c")
        base = tl(a, b, c)
        self.assertEqual(merge(base, tl(c, a, b), base)["timeline"], tl(c, a, b))

    def test_conflicting_order(self):
        a, b, c = clip("a"), clip("b"), clip("c")
        result = merge(tl(a, b, c), tl(b, a, c), tl(a, c, b))
        self.assertEqual(result["conflicts"][0]["path"], "order")

    def test_combined_trim_range_requires_clip_resolution(self):
        result = merge(tl(clip()), tl(clip(start=9)), tl(clip(end=5)))
        self.assertEqual(result["conflicts"][0]["path"], "clips/one")
        resolved = merge(
            tl(clip()), tl(clip(start=9)), tl(clip(end=5)), {"clips/one": "theirs"}
        )
        self.assertEqual(resolved["timeline"], tl(clip(end=5)))

    def test_merge_base_includes_both_parents(self):
        graph = {
            "a": {"parents": []},
            "b": {"parents": ["a"]},
            "c": {"parents": ["a"]},
            "d": {"parents": ["b", "c"]},
        }
        self.assertEqual(merge_base("d", "c", graph), "c")

    def test_criss_cross_rejected(self):
        graph = {
            "a": {"parents": []},
            "b": {"parents": ["a"]},
            "c": {"parents": ["a"]},
            "d": {"parents": ["b", "c"]},
            "e": {"parents": ["c", "b"]},
        }
        with self.assertRaises(ValueError):
            merge_base("d", "e", graph)

    def test_diff(self):
        rows = diff(tl(clip()), tl(clip(caption="hello")))
        self.assertEqual(
            rows, [{"clip": "one", "kind": "caption", "before": "", "after": "hello"}]
        )

    def test_hash_is_order_independent_for_objects(self):
        self.assertEqual(digest({"a": 1, "b": 2}), digest({"b": 2, "a": 1}))


if __name__ == "__main__":
    unittest.main()
