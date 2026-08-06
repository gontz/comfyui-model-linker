"""
Run the whole suite - Python and frontend.

    python tests/run.py            everything
    python tests/run.py -v         with each test named
    python tests/run.py --python   skip the frontend suites

The frontend suites need Node; without it they are reported as skipped rather
than failing the run, since the Python half stands on its own.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
EXTENSION_DIR = os.path.dirname(TESTS_DIR)


def run_python(verbosity: int) -> bool:
    # The modules under test log warnings for the malformed input several tests
    # deliberately feed them; that is the behaviour being checked, not output
    # worth reading.
    logging.disable(logging.CRITICAL)

    sys.path.insert(0, EXTENSION_DIR)
    suite = unittest.defaultTestLoader.discover(TESTS_DIR, top_level_dir=EXTENSION_DIR)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return result.wasSuccessful()


def run_frontend() -> bool:
    node = shutil.which('node')
    if not node:
        print('\nSKIPPED: the frontend suites need Node, which is not on PATH.')
        return True

    print('\nFrontend suites')
    # The child writes straight to the terminal, so flush first or the heading
    # lands after the output it introduces.
    sys.stdout.flush()
    completed = subprocess.run([node, os.path.join(TESTS_DIR, 'js', 'run.mjs')],
                               cwd=EXTENSION_DIR)
    return completed.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='name each test as it runs')
    parser.add_argument('--python', action='store_true',
                        help='skip the frontend suites')
    args = parser.parse_args()

    python_ok = run_python(2 if args.verbose else 1)
    frontend_ok = True if args.python else run_frontend()

    if python_ok and frontend_ok:
        print('\nAll suites passed.')
        return 0

    failed = [name for name, ok in (('Python', python_ok), ('frontend', frontend_ok)) if not ok]
    print(f"\nFAILED: {' and '.join(failed)}.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
