"""Worker-pool CLI regression tests, including the real HCMC oracle."""
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time
import unittest

ARGUMENTS = sys.argv[1:] if __name__ == '__main__' else []


class ConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if len(ARGUMENTS) != 2:
            raise unittest.SkipTest('Run via CTest with executable and project paths')
        cls.binary, cls.root = map(Path, ARGUMENTS)
        cls.fixture = cls.root / 'tests/fixtures'
        sys.path.insert(0, str(cls.root / 'python'))

    def command(self, algorithm='astar', threads=4, capacity=2, graph=None):
        return [str(self.binary), '--graph', str(graph or self.fixture / 'graph.txt'),
                '--algorithm', algorithm, '--threads', str(threads), '--queue-capacity', str(capacity)]

    def run_cli(self, text=None, **options):
        return subprocess.run(self.command(**options), input=text, capture_output=True,
                              text=True, timeout=30, cwd=tempfile.gettempdir())

    def test_fixture_repeated_queries_order_and_statuses(self):
        header = (self.fixture / 'queries.txt').read_text().splitlines()[:2]
        pairs = [(3, 3), (0, 4), (4, 0), (-1, 2), (2, 4), (5, 0), (0, 3), (6, 0), (0, 0)] * 20
        rows = [f'{1000-i} {u} {v}' for i, (u, v) in enumerate(pairs)]
        text = '\n'.join(header + [f'QUERIES {len(rows)}'] + rows) + '\n'
        for algorithm in ('dijkstra', 'dijkstra_baseline', 'astar', 'hpa', 'bihpa'):
            sequential = self.run_cli(text, algorithm=algorithm, threads=1)
            self.assertEqual(sequential.returncode, 1, sequential.stderr)
            for threads in (2, 4):
                with self.subTest(algorithm=algorithm, threads=threads):
                    parallel = self.run_cli(text, algorithm=algorithm, threads=threads, capacity=1)
                    self.assertEqual(parallel.returncode, sequential.returncode, parallel.stderr)
                    self.assertEqual(parallel.stdout, sequential.stdout)
                    self.assertEqual([int(row.split()[0]) for row in parallel.stdout.splitlines()[3:]],
                                     list(range(1000, 1000-len(rows), -1)))

    def test_hcmc_matches_sequential_and_oracle(self):
        from verification.check import check
        queries = self.root / 'benchmarks/queries.txt'
        for algorithm in ('dijkstra', 'dijkstra_baseline', 'astar', 'hpa', 'bihpa'):
            with self.subTest(algorithm=algorithm), tempfile.TemporaryDirectory() as tmp:
                outputs = []
                for threads in (1, 4):
                    run = subprocess.run(self.command(algorithm=algorithm, threads=threads,
                                                     graph=self.root / 'data/processed/graph.txt')
                                         + ['--queries', str(queries)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    outputs.append(run.stdout)
                self.assertEqual(outputs[0], outputs[1])
                result_path = Path(tmp) / 'results.txt'
                result_path.write_text(outputs[1])
                report = check(self.root / 'data/processed/graph.json', queries,
                               self.root / 'benchmarks/answers.json', result_path, 'any' if algorithm in ('hpa', 'bihpa') else 'optimal')
                self.assertTrue(report['ok'], report['errors'][:5])
                if algorithm in ('hpa', 'bihpa'):
                    self.assertLessEqual(report['max_gap_percent'], 5 + 1e-6)

    def test_streams_before_next_query_and_before_eof(self):
        header = (self.fixture / 'queries.txt').read_text().splitlines()[:2]
        proc = subprocess.Popen(self.command(capacity=1), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                data = bytearray()
                def wait_for(token):
                    deadline = time.monotonic() + 5
                    while token not in data:
                        remaining = deadline - time.monotonic()
                        self.assertGreater(remaining, 0, 'No result while stdin remains open')
                        self.assertTrue(selector.select(remaining), 'Timed out waiting for result')
                        chunk = os.read(proc.stdout.fileno(), 4096)
                        self.assertTrue(chunk, 'Process closed stdout prematurely')
                        data.extend(chunk)
                proc.stdin.write(('\n'.join(header) + '\nQUERIES 2\n41 3 3\n').encode())
                proc.stdin.flush()
                wait_for(b'41 found 0 1 3\n')
                proc.stdin.write(b'9 0 0\n')
                proc.stdin.flush()
                wait_for(b'9 found 0 1 0\n')
            proc.stdin.close()
            proc.stdin = None
            _, errors = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 0, errors.decode())
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)

    def test_parallel_errors_and_empty_input(self):
        original = (self.fixture / 'queries.txt').read_text()
        for threads in (0, -1, 'invalid'):
            self.assertEqual(self.run_cli(original, threads=threads).returncode, 1)
        for capacity in (0, -1, 'invalid'):
            self.assertEqual(self.run_cli(original, capacity=capacity).returncode, 1)
        bad_inputs = [original.replace('1 4 0', '0 4 0'), original + 'extra\n',
                      '\n'.join(original.splitlines()[:-1]) + '\n',
                      original.replace('0 0 4', '0 0 4 extra'),
                      original.replace(original.splitlines()[1], 'GRAPH_SHA256 ' + '0' * 64)]
        for text in bad_inputs:
            result = self.run_cli(text, capacity=1)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('Error:', result.stderr)
        empty = '\n'.join(original.splitlines()[:2]) + '\nQUERIES 0\n'
        result = self.run_cli(empty)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 3)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
