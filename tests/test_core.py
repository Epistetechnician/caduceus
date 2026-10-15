import json
import os
import random
import tempfile
import unittest

from caduceus import merkle, readback
from caduceus.digest import DigestError, canonical_bytes, domain_digest, loads_strict
from caduceus.ledger import Ledger, LedgerError
from caduceus.witness import FileWitness, WitnessError


def _rand_str(rng):
    alphabet = ["a", "Z", "0", " ", '"', "\\", "\n", "\t", "\x00", "\x1f", "\x7f", "é", "€", " ", "😀", "\U0010ffff"]
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 8)))


def _rand_value(rng, depth=0):
    choice = rng.randint(0, 6 if depth < 3 else 3)
    if choice == 0:
        return None
    if choice == 1:
        return rng.choice([True, False])
    if choice == 2:
        return rng.randint(-(2**70), 2**70)
    if choice == 3:
        return _rand_str(rng)
    if choice in (4, 5):
        return [_rand_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {_rand_str(rng): _rand_value(rng, depth + 1) for _ in range(rng.randint(0, 4))}


class DigestTests(unittest.TestCase):
    def test_floats_and_bad_keys_rejected(self):
        with self.assertRaises(DigestError):
            canonical_bytes({"a": 1.5})
        with self.assertRaises(DigestError):
            canonical_bytes({1: "a"})

    def test_strict_loads(self):
        with self.assertRaises(DigestError):
            loads_strict('{"a":1,"a":2}')
        with self.assertRaises(DigestError):
            loads_strict('{"a":1.0}')
        with self.assertRaises(DigestError):
            loads_strict('{"a":NaN}')

    def test_domain_separation(self):
        self.assertNotEqual(domain_digest("a", {"x": 1}), domain_digest("b", {"x": 1}))

    def test_differential_encoder(self):
        rng = random.Random(1234)
        for _ in range(500):
            value = _rand_value(rng)
            self.assertEqual(canonical_bytes(value).decode("ascii"), readback.enc(value))


class MerkleTests(unittest.TestCase):
    def _leaves(self, n):
        return [merkle.leaf_hash(("%064x" % i)) for i in range(n)]

    def test_inclusion_all_sizes(self):
        for n in range(1, 34):
            leaves = self._leaves(n)
            r = merkle.root(leaves)
            for i in range(n):
                proof = merkle.inclusion_proof(i, leaves)
                self.assertTrue(merkle.verify_inclusion(i, n, leaves[i], proof, r), (n, i))
                bad = merkle.verify_inclusion(i, n, leaves[(i + 1) % n], proof, r)
                self.assertEqual(bad, n == 1)

    def test_consistency_all_pairs(self):
        for n in range(1, 40):
            leaves = self._leaves(n)
            for m in range(0, n + 1):
                proof = merkle.consistency_proof(m, leaves)
                self.assertTrue(
                    merkle.verify_consistency(
                        m, n, merkle.root(leaves[:m]), merkle.root(leaves), proof
                    ),
                    (m, n),
                )

    def test_consistency_rejects_forked_history(self):
        a = self._leaves(10)
        b = list(a)
        b[2] = merkle.leaf_hash("f" * 64)
        proof = merkle.consistency_proof(6, b)
        self.assertFalse(
            merkle.verify_consistency(6, 10, merkle.root(a[:6]), merkle.root(b), proof)
        )

    def test_iterative_root_matches_recursive(self):
        for n in range(0, 70):
            digests = ["%064x" % (i * 7919) for i in range(n)]
            leaves = [merkle.leaf_hash(d) for d in digests]
            self.assertEqual(merkle.root_hex(leaves), readback._merkle_root(digests))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "l.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_readback_agree(self):
        led = Ledger(self.path)
        for i in range(13):
            led.append("audit_note", {"i": i})
        head = led.head()
        rb = readback.verify_file(self.path)
        self.assertEqual((head.size, head.root, head.last_digest), (rb["size"], rb["root"], rb["last_digest"]))
        reloaded = Ledger(self.path)
        self.assertEqual(reloaded.head(), head)

    def test_tamper_detected_by_both(self):
        led = Ledger(self.path)
        for i in range(4):
            led.append("audit_note", {"i": i})
        with open(self.path, "r") as fh:
            lines = fh.readlines()
        lines[1] = lines[1].replace('"i":1', '"i":9')
        with open(self.path, "w") as fh:
            fh.writelines(lines)
        with self.assertRaises(LedgerError):
            Ledger(self.path)
        with self.assertRaises(readback.ReadbackError):
            readback.verify_file(self.path)

    def test_reordered_and_truncated(self):
        led = Ledger(self.path)
        for i in range(3):
            led.append("audit_note", {"i": i})
        with open(self.path) as fh:
            lines = fh.readlines()
        with open(self.path, "w") as fh:
            fh.writelines([lines[1], lines[0], lines[2]])
        with self.assertRaises(LedgerError):
            Ledger(self.path)
        with open(self.path, "w") as fh:
            fh.writelines(lines[:2])
        self.assertEqual(Ledger(self.path).head().size, 2)  # truncation is valid on its own...
        w = os.path.join(self.tmp.name, "w.jsonl")
        # ...which is exactly why a witness is needed:
        full = Ledger(os.path.join(self.tmp.name, "f.jsonl"))
        for i in range(3):
            full.append("audit_note", {"i": i})
        wit = FileWitness(w)
        wit.observe(full)
        trunc = Ledger(os.path.join(self.tmp.name, "t.jsonl"))
        for i in range(2):
            trunc.append("audit_note", {"i": i})
        with self.assertRaises(WitnessError):
            wit.observe(trunc)

    def test_rejects_raw_payloads_floats_unknown_kind(self):
        led = Ledger(self.path)
        with self.assertRaises(LedgerError):
            led.append("audit_note", {"transcript": "hello"})
        with self.assertRaises(LedgerError):
            led.append("audit_note", {"x": "a" * 5000})
        with self.assertRaises(LedgerError):
            led.append("audit_note", {"x": 1.5})
        with self.assertRaises(LedgerError):
            led.append("not_a_kind", {})
        self.assertEqual(len(led), 0)

    def test_concurrent_writer_detected(self):
        a = Ledger(self.path)
        a.append("audit_note", {"i": 0})
        b = Ledger(self.path)
        b.append("audit_note", {"i": 1})
        with self.assertRaises(LedgerError):
            a.append("audit_note", {"i": 2})

    def test_witness_accepts_extension_and_detects_fork(self):
        led = Ledger(self.path)
        w = FileWitness(os.path.join(self.tmp.name, "w.jsonl"))
        for i in range(5):
            led.append("audit_note", {"i": i})
            w.observe(led)
        fork = Ledger(os.path.join(self.tmp.name, "fork.jsonl"))
        for i in range(7):
            fork.append("audit_note", {"i": 100 + i})
        with self.assertRaises(WitnessError):
            w.observe(fork)

    def test_inclusion_proof_from_ledger(self):
        led = Ledger(self.path)
        for i in range(9):
            led.append("audit_note", {"i": i})
        head = led.head()
        for seq in range(9):
            proof = [bytes.fromhex(p) for p in led.inclusion_proof(seq)]
            leaf = merkle.leaf_hash(led.entries[seq]["digest"])
            self.assertTrue(merkle.verify_inclusion(seq, 9, leaf, proof, bytes.fromhex(head.root)))


if __name__ == "__main__":
    unittest.main()
