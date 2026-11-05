"""A browser signs; Python verifies. Runs the app's own message builder under node, generates a WebCrypto Ed25519 key there, enrols its public key, signs the held
change's approval message in node, and has the real kernel release the change with that signature. Skips where node or Ed25519 WebCrypto is unavailable."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from caduceus import approvals
from caduceus.transition import Kernel, Policy

APP_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "caduceus_governance", "ui", "src", "app.js")
NODE = shutil.which("node")
SKILL = "---\nname: a\ndescription: a\n---\nstep\n"

NODE_PROG = r'''
const vm = require("vm"), fs = require("fs"), readline = require("readline"), { webcrypto } = require("crypto"), subtle = webcrypto.subtle;
const msgFn = vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8") + ";caduceusApprovalMessage", {});
const hex = (b) => Buffer.from(b).toString("hex");
(async () => {
  let kp;
  try { kp = await subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]); } catch (e) { console.log(JSON.stringify({ unsupported: true })); return; }
  const pub = hex(await subtle.exportKey("raw", kp.publicKey));
  const fp = hex(await subtle.digest("SHA-256", Buffer.from(pub, "hex"))).slice(0, 16).match(/.{4}/g).join("-");
  console.log(JSON.stringify({ pub, fp }));
  const rl = readline.createInterface({ input: process.stdin });
  for await (const line of rl) {
    const j = JSON.parse(line);
    const msg = msgFn(j.action, j.surface, j.effect, j.parent, j.nonce, j.expires);
    const sig = hex(await subtle.sign({ name: "Ed25519" }, kp.privateKey, new TextEncoder().encode(msg)));
    console.log(JSON.stringify({ msg, sig }));
  }
})();
'''


@unittest.skipUnless(NODE, "node not installed")
class BrowserSignsPythonVerifies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = subprocess.Popen([NODE, "-e", NODE_PROG, APP_JS], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        first = json.loads(self.proc.stdout.readline())
        if first.get("unsupported"):
            self.proc.kill()
            self.skipTest("this node has no Ed25519 WebCrypto")
        self.pub, self.fp = first["pub"], first["fp"]

    def tearDown(self):
        self.proc.kill()
        self.tmp.cleanup()

    def sign(self, **kw):
        self.proc.stdin.write(json.dumps(kw) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def test_a_webcrypto_signature_releases_a_held_change_and_the_messages_are_byte_identical(self):
        root, base = os.path.join(self.tmp.name, "skills"), os.path.join(self.tmp.name, "c")
        os.makedirs(os.path.join(root, "dev", "a"))
        open(os.path.join(root, "dev", "a", "SKILL.md"), "w").write(SKILL)
        k = Kernel(root, base, Policy(approval_required=True), clock=lambda: int(time.time()))
        k.reconcile()
        k.observe_taint("s", "web_extract", "page")
        open(os.path.join(root, "dev", "a", "SKILL.md"), "a").write("a learned step\n")
        ed = k.reconcile(taint=k.taint_scope("s"))["verdict"]["effect_digest"]
        self.assertEqual(approvals.add_device(base, "phone", self.pub, "op")["fingerprint"], self.fp)  # JS and Python agree on the fingerprint
        parent, nonce, expires = k.review(ed)["parent"], "ab" * 16, int(time.time()) + 300
        out = self.sign(action="release", surface="skills", effect=ed, parent=parent, nonce=nonce, expires=expires)
        self.assertEqual(out["msg"].encode(), approvals.message("release", "skills", ed, parent, nonce, expires))  # same bytes from the app's JS and from Python
        res = k.release(ed, "ignored", "from the browser", {"device": "phone", "nonce": nonce, "expires": expires, "sig": out["sig"]})
        self.assertEqual((res["status"], res["actor"]), ("released", "device:phone"))
        self.assertIn("a learned step", open(os.path.join(root, "dev", "a", "SKILL.md")).read())

    def test_a_signature_made_for_release_cannot_discard(self):
        root, base = os.path.join(self.tmp.name, "skills"), os.path.join(self.tmp.name, "c")
        os.makedirs(os.path.join(root, "dev", "a"))
        open(os.path.join(root, "dev", "a", "SKILL.md"), "w").write(SKILL)
        k = Kernel(root, base, Policy(approval_required=True), clock=lambda: int(time.time()))
        k.reconcile()
        k.observe_taint("s", "web_extract", "page")
        open(os.path.join(root, "dev", "a", "SKILL.md"), "a").write("x\n")
        ed = k.reconcile(taint=k.taint_scope("s"))["verdict"]["effect_digest"]
        approvals.add_device(base, "phone", self.pub, "op")
        parent, nonce, expires = k.review(ed)["parent"], "cd" * 16, int(time.time()) + 300
        sig = self.sign(action="release", surface="skills", effect=ed, parent=parent, nonce=nonce, expires=expires)["sig"]
        with self.assertRaises(Exception):
            k.discard(ed, "x", "swap", {"device": "phone", "nonce": nonce, "expires": expires, "sig": sig})


if __name__ == "__main__":
    unittest.main()
