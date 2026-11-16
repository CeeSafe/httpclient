#!/usr/bin/env python3
"""Writes tests/data/: bodies compressed by Python's zlib and gzip, for tests/inflate.cee.

usage: python3 tools/gen_inflate_vectors.py

For each case `name`, `name.raw` is the text and `name.gz`, `name.zz` (zlib) and `name.df` (bare DEFLATE) are the
same text compressed. Python's zlib is the oracle: the decoder under test must give back `name.raw` from each. The
texts are made from a fixed seed, so the files are the same on every run with the same zlib.

  empty    no bytes
  hello    a short text: a fixed-codes block
  stored   compressed at level 0: stored blocks
  text     200 kB of words: dynamic codes, many blocks
  noise    64 kB of random bytes, which do not compress: stored blocks inside a gzip made at level 9
  skew     60 kB of bytes with steeply falling frequencies: codes longer than the fast table holds
  run      100 kB of one byte: matches that overlap what they write
  small    a few hundred bytes with dynamic codes, for the corruption tests
  members  two gzip members one after the other (only .gz)
  flags    a gzip member with every optional header field (only .gz)
"""
import gzip, pathlib, random, struct, zlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "tests" / "data"


def deflate(data, level=9, wbits=-15):
    c = zlib.compressobj(level, zlib.DEFLATED, wbits)
    return c.compress(data) + c.flush()


def write(name, data, level=9):
    (OUT / f"{name}.raw").write_bytes(data)
    (OUT / f"{name}.gz").write_bytes(deflate(data, level, 31))
    (OUT / f"{name}.zz").write_bytes(deflate(data, level, 15))
    (OUT / f"{name}.df").write_bytes(deflate(data, level, -15))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261116)
    words = ["the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog", "http", "client", "response", "header",
             "content", "encoding", "gzip", "deflate", "window", "stream", "buffer", "connection"]
    text = " ".join(rng.choice(words) + (str(rng.randrange(1000)) if rng.random() < 0.2 else "")
                    for _ in range(40000)).encode()[:200000]
    write("empty", b"")
    write("hello", b"hello, hello, hello world\n")
    write("stored", text[:70000], level=0)
    write("text", text)
    write("noise", bytes(rng.randrange(256) for _ in range(65536)))
    write("run", b"a" * 100000)
    # Byte values whose frequencies fall off steeply, without repeats to match: the rare ones get long codes.
    weights = [0.93 ** k for k in range(220)]
    write("skew", bytes(rng.choices(range(220), weights)[0] for _ in range(60000)))
    write("small", text[:600], level=9)
    (OUT / "members.raw").write_bytes(b"first member, " + text[:1000])
    (OUT / "members.gz").write_bytes(deflate(b"first member, ", 9, 31) + deflate(text[:1000], 6, 31))
    # A member with FEXTRA, FNAME, FCOMMENT and FHCRC set.
    body = b"every optional field of the header is present\n"
    head = bytes([0x1F, 0x8B, 8, 2 | 4 | 8 | 16, 0, 0, 0, 0, 0, 255]) + struct.pack("<H", 5) + b"extra" + b"name.txt\0" + \
        b"a comment\0"
    head += struct.pack("<H", zlib.crc32(head) & 0xFFFF)
    (OUT / "flags.raw").write_bytes(body)
    (OUT / "flags.gz").write_bytes(head + deflate(body) + struct.pack("<II", zlib.crc32(body), len(body)))
    for name in ("members", "flags"):
        assert gzip.decompress((OUT / f"{name}.gz").read_bytes()) == (OUT / f"{name}.raw").read_bytes()
    print(f"{OUT}: {len(list(OUT.iterdir()))} files")


if __name__ == "__main__":
    main()
