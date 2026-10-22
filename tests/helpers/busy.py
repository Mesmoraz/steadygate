"""Burn CPU for argv[1] seconds so collector tests have a real, busy process to sample."""

import sys
import time

end = time.monotonic() + float(sys.argv[1])
while time.monotonic() < end:
    sum(range(10**5))
