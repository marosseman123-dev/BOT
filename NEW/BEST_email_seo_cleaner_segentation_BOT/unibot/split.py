"""Split a huge domain list into numbered chunks so each can run as its own --run
(keeps every DB + output set bounded instead of one 150 GB database).

    python -m unibot.split input/domains.txt 5000000
    -> input/domains_part_0001.txt, _0002.txt, ...  (5,000,000 domains each)

Streaming: never loads the whole file into memory.
"""
from __future__ import annotations

import os
import sys


def main():
    if len(sys.argv) < 2:
        print("usage: python -m unibot.split <file.txt> [lines_per_part=5000000]")
        return
    path = sys.argv[1]
    per = int(sys.argv[2]) if len(sys.argv) > 2 else 5_000_000
    base, ext = os.path.splitext(path)
    ext = ext or ".txt"
    idx, n, total, out = 0, 0, 0, None
    with open(path, encoding="utf-8-sig", errors="ignore") as f:
        for line in f:
            if out is None or n >= per:
                if out:
                    out.close()
                idx += 1
                out = open(f"{base}_part_{idx:04d}{ext}", "w", encoding="utf-8")
                n = 0
            out.write(line)
            n += 1
            total += 1
    if out:
        out.close()
    print(f"split {total:,} lines into {idx} part(s) of up to {per:,} each")


if __name__ == "__main__":
    main()
