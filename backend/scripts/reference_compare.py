"""Compare a municipally supplied reference SWMM model against our build of the same AOI.

    backend/.venv/bin/python backend/scripts/reference_compare.py \
        /path/to/reference.inp /path/to/result_package \
        --inp-crs EPSG:26910 --json comparison.json

Prints the comparison + reproduction report (outlet agreement, per-node load bias,
service-area IoU, invert profile, diameter distribution — numbers, not verdicts) and
optionally writes the structured JSON. The reference `.inp` is a confidential local input:
it is read, never copied, and must never enter the repository.

All of the logic lives in `swmmcanada.reference_compare` so the metrics are importable and
unit-tested; this file is only the command-line entry point.
"""
import sys

from swmmcanada.reference_compare import main

if __name__ == "__main__":
    sys.exit(main())
