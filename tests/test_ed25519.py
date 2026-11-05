import hashlib
import os
import tempfile
import unittest

from caduceus import ed25519, signing
from caduceus.ledger import Ledger

VECTORS = [  # RFC 8032 section 7.1, tests 1 and 2
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]
D = hashlib.sha256(b"candidate").hexdigest()


class Ed25519Tests(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for seed, pub, msg, sig in VECTORS:
            seed, pub, msg, sig = map(bytes.fromhex, (seed, pub, msg, sig))
            self.assertEqual(ed25519.public_key(seed), pub)
            self.assertEqual(ed25519.sign(seed, msg), sig)
            self.assertTrue(ed25519.verify(pub, msg, sig))

    def test_tampering_is_rejected(self):
        seed, pub, msg, sig = (bytes.fromhex(x) for x in VECTORS[1])
        self.assertFalse(ed25519.verify(pub, msg + b"x", sig))
        self.assertFalse(ed25519.verify(pub, msg, sig[:-1] + bytes([sig[-1] ^ 1])))
        self.assertFalse(ed25519.verify(pub, msg, sig[:63]))
        self.assertFalse(ed25519.verify(bytes(32), msg, sig))
        self.assertFalse(ed25519.verify(pub, msg, sig[:32] + (ed25519.L).to_bytes(32, "little")))  # non-canonical s

    def test_cross_check_with_an_independent_implementation_when_present(self):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
        except ImportError:
            self.skipTest("independent implementation not installed")
        for i in range(5):
            seed, msg = os.urandom(32), os.urandom(i * 17)
            k = Ed25519PrivateKey.from_private_bytes(seed)
            self.assertEqual(ed25519.public_key(seed), k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
            self.assertEqual(ed25519.sign(seed, msg), k.sign(msg))


class SigningSchemeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ring = os.path.join(self.tmp.name, "ring.json")
        self.led = Ledger(os.path.join(self.tmp.name, "l.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_ed25519_sign_and_verify_with_a_public_only_keyring(self):
        signing.add_signer(self.ring, "alice", "reviewer", "ed25519")
        signing.sign(self.led, self.ring, "alice", D)
        pub = os.path.join(self.tmp.name, "pub.json")
        self.assertEqual(signing.export_public(self.ring, pub), 1)
        self.assertNotIn("seed_hex", open(pub).read())
        self.assertEqual(signing.verified_signatures(self.led, pub, D), [{"signer": "alice", "role": "reviewer"}])
        with self.assertRaises(signing.SigningError):
            signing.sign(self.led, pub, "alice", D)  # a verifier host cannot sign

    def test_forged_or_swapped_signature_is_an_incident(self):
        signing.add_signer(self.ring, "alice", "reviewer", "ed25519")
        signing.add_signer(self.ring, "mallory", "reviewer", "ed25519")
        body = signing.sign(self.led, self.ring, "mallory", D)
        # mallory's valid signature replayed under alice's name
        self.led.append("review_signature", {**body, "signer": "alice"})
        with self.assertRaises(signing.SigningError):
            signing.verified_signatures(self.led, self.ring, D)

    def test_hmac_still_works_and_hmac_is_never_exported(self):
        signing.add_signer(self.ring, "bob", "reviewer")
        signing.sign(self.led, self.ring, "bob", D)
        self.assertEqual(signing.verified_signatures(self.led, self.ring, D), [{"signer": "bob", "role": "reviewer"}])
        self.assertEqual(signing.export_public(self.ring, os.path.join(self.tmp.name, "p.json")), 0)

    def test_public_only_ring_cannot_verify_an_hmac_record(self):
        signing.add_signer(self.ring, "bob", "reviewer")
        signing.sign(self.led, self.ring, "bob", D)
        signing.add_signer(self.ring, "alice", "reviewer", "ed25519")
        pub = os.path.join(self.tmp.name, "p.json")
        signing.export_public(self.ring, pub)
        with self.assertRaises(signing.SigningError):
            signing.verified_signatures(self.led, pub, D)

    def test_tampered_keyring_pub_is_rejected(self):
        import json
        signing.add_signer(self.ring, "alice", "reviewer", "ed25519")
        d = json.load(open(self.ring))
        d["alice"]["pub_hex"] = "00" * 32
        json.dump(d, open(self.ring, "w"))
        os.chmod(self.ring, 0o600)
        with self.assertRaises(signing.SigningError):
            signing.load_keyring(self.ring)


if __name__ == "__main__":
    unittest.main()
