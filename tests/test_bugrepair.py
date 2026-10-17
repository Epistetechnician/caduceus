import unittest

from caduceus import bugrepair as b


class Edit(unittest.TestCase):
    def test_roundtrip(self):
        t = "noise\n" + b.format_edit("x < 3", "x >= 3") + "\ntrailer"
        self.assertEqual(b.parse_edit(t), ("x < 3", "x >= 3"))

    def test_fences_and_diff_style_accepted(self):
        self.assertEqual(b.parse_edit("```diff\n-    a < 1\n+    a >= 1\n```"), ("    a < 1", "    a >= 1"))
        self.assertEqual(b.parse_edit("```\n" + b.format_edit("x", "y") + "\n```"), ("x", "y"))

    def test_malformed_is_none(self):
        self.assertIsNone(b.parse_edit("<<<<<<< SEARCH\na\n=======\nb"))
        self.assertIsNone(b.parse_edit("no block"))

    def test_apply_requires_unique_match_and_change(self):
        self.assertEqual(b.apply_edit("a\nb\n", "a", "c"), "c\nb\n")
        self.assertIsNone(b.apply_edit("a\na\n", "a", "c"))
        self.assertIsNone(b.apply_edit("a\n", "z", "c"))
        self.assertIsNone(b.apply_edit("a\n", "a", "a"))

    def test_apply_tolerates_indentation(self):
        src = "def f(x):\n    if x < 1:\n        return 0\n"
        self.assertEqual(b.apply_edit(src, "if x < 1:", "if x <= 1:"), "def f(x):\n    if x <= 1:\n        return 0\n")
        self.assertIsNone(b.apply_edit("a\n  a\n", "a", "b"))

    def test_diff_edit_reproduces_fix(self):
        bug, fix = "def f(x):\n    if x > 1:\n        return 1\n    return 0\n", "def f(x):\n    if x <= 1:\n        return 1\n    return 0\n"
        s, r = b.diff_edit(bug, fix)
        self.assertEqual(b.apply_edit(bug, s, r), fix)


class Stats(unittest.TestCase):
    def test_bootstrap_deterministic_and_ordered(self):
        d = [b.MICRO] * 8 + [0] * 4 + [-b.MICRO]
        x, y = b.bootstrap(d, 1, 500), b.bootstrap(d, 1, 500)
        self.assertEqual(x, y)
        self.assertLessEqual(x["lower_micro"], x["mean_micro"])
        self.assertLessEqual(x["mean_micro"], x["upper_micro"])

    def passes(self, cand, other):
        ids = ["t%d" % i for i in range(20)]
        return {"candidate": {t: cand for t in ids}, "untouched_base": {t: other for t in ids},
                "fixed_policy": {t: other for t in ids}, "deterministic_random": {t: other for t in ids}}

    def test_decision_rule(self):
        win = b.contrasts(self.passes(1, 0), 1, 200)
        lose = b.contrasts(self.passes(0, 1), 1, 200)
        tie = b.contrasts(self.passes(1, 1), 1, 200)
        self.assertEqual(b.decide([win, win, win], True, 100000)["verdict"], "Pass")
        self.assertEqual(b.decide([win, win, tie], True, 100000)["verdict"], "Pass")
        self.assertEqual(b.decide([win, tie, tie], True, 100000)["verdict"], "Inconclusive")
        self.assertEqual(b.decide([win, win, lose], True, 100000)["verdict"], "Fail")
        self.assertEqual(b.decide([win, win, win], False, 100000)["verdict"], "Fail")


if __name__ == "__main__":
    unittest.main()
