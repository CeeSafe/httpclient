#!/usr/bin/env python3
"""Writes src/huffman.bin, the table HPACK's Huffman decoder walks (RFC 7541 §5.2 and Appendix B).

usage: python3 tools/gen_huffman.py [path/to/nghttp3_qpack_huffman_data.c]

The code of each of the 257 symbols (a length in bits and the bits) is read from nghttp3's `huffman_sym_table`
(CeeSafe.Web's third_party/nghttp3, the default path), which is Appendix B's table. From it this builds the code's
binary tree, numbers its 256 inner nodes (the root is 0), and writes for each node and each 4-bit piece of input
three bytes:

    next   the node after these four bits
    flags  1: a symbol was completed on the way (at most one, since no code is shorter than 5 bits)
           2: `next` is a place where a string may end: the bits since the last symbol are fewer than 8 and all 1
              (a prefix of EOS, §5.2)
           4: these bits cannot occur: they complete EOS
    symbol the completed symbol, if flag 1

256 nodes x 16 pieces x 3 bytes = 12,288 bytes. The decoder takes two steps for each input byte.

Before writing, the table is checked against RFC 7541's own examples (Appendix C.4 and C.6): each string encoded
with the codes must give the RFC's bytes, and decoded through the generated table must give the string back.
"""
import pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT = HERE.parent.parent.parent / "CeeSafe.Web" / "third_party" / "nghttp3-1.19.0" / "lib" / "nghttp3_qpack_huffman_data.c"

RFC = {  # RFC 7541 Appendix C.4.1, C.4.2, C.4.3, C.6.1, C.6.3
    b"www.example.com": "f1e3c2e5f23a6ba0ab90f4ff",
    b"no-cache": "a8eb10649cbf",
    b"custom-key": "25a849e95ba97d7f",
    b"custom-value": "25a849e95bb8e8b4bf",
    b"302": "6402",
    b"private": "aec3771a4b",
    b"Mon, 21 Oct 2013 20:13:21 GMT": "d07abe941054d444a8200595040b8166e082a62d1bff",
    b"https://www.example.com": "9d29ad171863c78f0b97c8e9ae82ae43d3",
    b"foo=ASDJKHQKBZXOQWEOPIUAXQWEOIU; max-age=3600; version=1":
        "94e7821dd7f2e6c7b335dfdfcd5b3960d5af27087f3672c1ab270fb5291f9587316065c003ed4ee5b1063d5007",
}


def codes(path):
    text = pathlib.Path(path).read_text()
    body = text[text.index("huffman_sym_table[]"):]
    body = body[:body.index("};")]
    pairs = re.findall(r"\{\s*(\d+)\s*,\s*0x([0-9A-Fa-f]+)U?\s*\}", body)
    if len(pairs) != 257:
        sys.exit(f"expected 257 symbols, found {len(pairs)}")
    return [(int(n), int(c, 16) >> (32 - int(n))) for n, c in pairs]


def build(table):
    # A node is [zero, one, symbol]; a leaf has a symbol and no children.
    nodes = [[None, None, None]]
    for symbol, (length, code) in enumerate(table):
        at = 0
        for k in range(length - 1, -1, -1):
            bit = (code >> k) & 1
            if nodes[at][bit] is None:
                nodes.append([None, None, None])
                nodes[at][bit] = len(nodes) - 1
            at = nodes[at][bit]
        nodes[at][2] = symbol
    inner = [i for i, n in enumerate(nodes) if n[2] is None]
    if len(inner) != 256 or inner[0] != 0:
        sys.exit(f"expected 256 inner nodes, found {len(inner)}")
    number = {node: k for k, node in enumerate(inner)}
    # Where a string may end: the root, and the nodes up to 7 one-bits below it.
    accept = {0}
    at = 0
    for _ in range(7):
        at = nodes[at][1]
        if nodes[at][2] is not None:
            break
        accept.add(at)
    out = bytearray()
    for node in inner:
        for piece in range(16):
            at, flags, symbol = node, 0, 0
            for k in (3, 2, 1, 0):
                at = nodes[at][(piece >> k) & 1]
                if nodes[at][2] is not None:
                    if nodes[at][2] == 256:
                        flags |= 4
                    else:
                        flags |= 1
                        symbol = nodes[at][2]
                    at = 0
            if flags & 4:
                out += bytes([0, 4, 0])
                continue
            if at in accept:
                flags |= 2
            out += bytes([number[at], flags, symbol])
    return bytes(out)


def encode(table, text):
    bits, count = 0, 0
    for b in text:
        length, code = table[b]
        bits = (bits << length) | code
        count += length
    pad = (8 - count % 8) % 8
    bits = (bits << pad) | ((1 << pad) - 1)
    return bits.to_bytes((count + pad) // 8, "big")


def decode(states, data):
    node, out, ok = 0, bytearray(), True
    for byte in data:
        for piece in (byte >> 4, byte & 15):
            at = (node * 16 + piece) * 3
            node, flags, symbol = states[at], states[at + 1], states[at + 2]
            if flags & 4:
                return None
            if flags & 1:
                out.append(symbol)
            ok = bool(flags & 2)
    return bytes(out) if ok else None


def main():
    table = codes(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)
    states = build(table)
    for text, expected in RFC.items():
        got = encode(table, text).hex()
        if got != expected:
            sys.exit(f"{text!r}: encoded {got}, the RFC has {expected}")
        if decode(states, bytes.fromhex(expected)) != text:
            sys.exit(f"{text!r}: does not decode back")
    every = bytes(range(256)) * 2
    if decode(states, encode(table, every)) != every:
        sys.exit("the 256 byte values do not survive a round trip")
    target = HERE.parent / "src" / "huffman.bin"
    target.write_bytes(states)
    print(f"{target}: {len(states)} bytes")


if __name__ == "__main__":
    main()
