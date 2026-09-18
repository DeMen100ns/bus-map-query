"""Selection/quality rules must not quietly choose inaccurate or partial runs."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tune_hpa import WEIGHTS, candidates, choose, quality_pass, quality_summary, top_sizes


class TuningTests(unittest.TestCase):
    def test_quality_uses_unique_gaps_and_rejects_undefined(self):
        gaps = [{'gap_percent': v, 'optimal': v == 0, 'extra_distance_m': v} for v in [0]*95+[2]*4+[5]]
        q = quality_summary(gaps)
        self.assertEqual(q['p95_gap_percent'], 0)
        self.assertTrue(quality_pass(q))
        self.assertFalse(quality_pass({**q, 'undefined_relative_gap_count': 1}))
        self.assertFalse(quality_pass({**q, 'max_gap_percent': 6}))

    def test_tie_band_and_distinct_sizes(self):
        def row(size, score, p95):
            return dict(size=size, weight=1.05, score_ms=score, p95_ms=p95, index_bytes=100, build_ms=1)
        values=[row(1000, 1, 3),row(2000,1.02,2),row(4000,1.1,1)]
        self.assertEqual(choose(values)['size'],2000)
        self.assertEqual(top_sizes(values),[2000,1000])
        self.assertIsNone(choose([]))

    def test_only_weighted_candidates(self):
        self.assertTrue(all(1 < w <= 1.05 for w in WEIGHTS))
        self.assertEqual(candidates([{'ok': True, 'algorithm': 'hpa', 'weight': 1}]), [])

    def test_bad_run_is_not_a_candidate(self):
        self.assertEqual(candidates([{'ok':False}]),[])


if __name__ == '__main__':
    unittest.main()
