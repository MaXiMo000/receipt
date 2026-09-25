"""python3 tests/test_providence.py

No framework -- assert-based, run as __main__, matching the rest of the
portfolio's test style (see satchel/receipt/invariant).
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from receipt_evidence.providence.check import check_bundle
from receipt_evidence.providence.convert import convert_invariant, convert_receipt
from receipt_evidence.providence.spec import canonical_hash


def test_single_file_conformant():
    with tempfile.TemporaryDirectory() as d:
        payload = {"a": 1, "b": [1, 2, 3]}
        doc = {
            "providence_version": 1,
            "generated_at": 1.0,
            "tool": "test",
            "payload": payload,
            "sha256": canonical_hash(payload),
        }
        p = pathlib.Path(d) / "bundle.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        assert check_bundle(p) == [], check_bundle(p)


def test_single_file_tampered():
    with tempfile.TemporaryDirectory() as d:
        payload = {"a": 1}
        doc = {
            "providence_version": 1,
            "generated_at": 1.0,
            "tool": "test",
            "payload": payload,
            "sha256": canonical_hash(payload),
        }
        p = pathlib.Path(d) / "bundle.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        # Edit the payload after the manifest's hash was computed -- the
        # exact scenario this whole thing exists to catch.
        tampered = json.loads(p.read_text())
        tampered["payload"]["a"] = 999
        p.write_text(json.dumps(tampered), encoding="utf-8")
        issues = check_bundle(p)
        assert len(issues) == 1 and "mismatch" in issues[0], issues


def test_directory_conformant():
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        item_bytes = json.dumps({"result": "ok"}).encode("utf-8")
        (d / "check-a.json").write_bytes(item_bytes)
        import hashlib
        manifest = {
            "providence_version": 1,
            "generated_at": 1.0,
            "tool": "test",
            "items": [{"id": "check-a", "sha256": hashlib.sha256(item_bytes).hexdigest(), "status": "pass"}],
        }
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        assert check_bundle(d) == [], check_bundle(d)


def test_directory_tampered_file_detected():
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        item_bytes = json.dumps({"result": "ok"}).encode("utf-8")
        (d / "check-a.json").write_bytes(item_bytes)
        import hashlib
        manifest = {
            "providence_version": 1,
            "generated_at": 1.0,
            "tool": "test",
            "items": [{"id": "check-a", "sha256": hashlib.sha256(item_bytes).hexdigest()}],
        }
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        # Tamper with the evidence file after the manifest was written.
        (d / "check-a.json").write_bytes(json.dumps({"result": "tampered"}).encode("utf-8"))
        issues = check_bundle(d)
        assert len(issues) == 1 and "mismatch" in issues[0], issues


def test_directory_missing_file_detected():
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        manifest = {
            "providence_version": 1,
            "generated_at": 1.0,
            "tool": "test",
            "items": [{"id": "ghost", "sha256": "0" * 64}],
        }
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        issues = check_bundle(d)
        assert len(issues) == 1 and "no file" in issues[0], issues


def test_orphan_file_flagged():
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        manifest = {"providence_version": 1, "generated_at": 1.0, "tool": "test", "items": []}
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (d / "mystery.json").write_text("{}", encoding="utf-8")
        issues = check_bundle(d)
        assert len(issues) == 1 and "isn't referenced" in issues[0], issues


def test_convert_invariant_real_shape_round_trips():
    """A hand-built fixture matching invariant/evidence.py's actual write()
    output byte-for-shape (not imported cross-repo -- see README)."""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        src = d / "evidence"
        src.mkdir()
        import hashlib
        check_blob = json.dumps({"query": "SELECT 1", "rows": 0}, indent=2, sort_keys=True).encode("utf-8")
        (src / "no_negative_payments.json").write_bytes(check_blob)
        (src / "manifest.json").write_text(json.dumps({
            "generated_at": 1757430000.0,
            "checks": [{
                "name": "no_negative_payments",
                "status": "pass",
                "detail": "",
                "seconds": 0.01,
                "sha256": hashlib.sha256(check_blob).hexdigest(),
            }],
        }), encoding="utf-8")

        out = convert_invariant(src, d / "out")
        assert check_bundle(out) == [], check_bundle(out)


def test_convert_receipt_real_shape_round_trips():
    """A hand-built fixture matching receipt/evidence.py's actual write()
    output shape."""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        result = {"status": "pass", "changed": [], "declared": ["src/**"]}
        blob = json.dumps(result, indent=2, sort_keys=True, default=str).encode("utf-8")
        import hashlib
        receipt_file = d / "20260909T000000Z-abcd1234.json"
        receipt_file.write_text(json.dumps({
            "schema_version": 1,
            "receipt": result,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "written_at": 1757430000.0,
        }), encoding="utf-8")

        out = convert_receipt(receipt_file, d / "out")
        assert check_bundle(out) == [], check_bundle(out)


def test_non_object_top_level_json_is_reported_not_raised():
    """A malformed or hostile bundle can be valid JSON that isn't an object
    at all -- a bare list, string, or null. check_bundle's own module
    docstring promises it never raises on a malformed bundle; this is the
    shape that used to break that promise with an AttributeError/TypeError
    instead of a reported issue."""
    with tempfile.TemporaryDirectory() as d:
        for content in ("[1, 2, 3]", '"just a string"', "null", "42"):
            p = pathlib.Path(d) / "bundle.json"
            p.write_text(content, encoding="utf-8")
            issues = check_bundle(p)  # must not raise
            assert issues, f"{content!r} should be flagged, not silently pass"


def test_manifest_items_that_are_not_objects_are_reported_not_raised():
    with tempfile.TemporaryDirectory() as d:
        manifest = {"providence_version": 1, "generated_at": 1.0, "tool": "test",
                    "items": [1, "bad", None]}
        (pathlib.Path(d) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        issues = check_bundle(d)  # must not raise
        assert len(issues) == 3, issues


def test_an_id_that_escapes_the_bundle_directory_is_rejected():
    # A real file outside the bundle, with a correct hash in the manifest:
    # before the fix this read ../outside/secret.json and reported PASS.
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "outside").mkdir()
        (root / "bundle").mkdir()
        secret = root / "outside" / "secret.json"
        secret.write_text('{"x": 1}', encoding="utf-8")
        import hashlib
        digest = hashlib.sha256(secret.read_bytes()).hexdigest()
        for bad_id in ("../outside/secret", str(root / "outside" / "secret"), "..", 7):
            manifest = {"providence_version": 1, "generated_at": 1.0, "tool": "test",
                        "items": [{"id": bad_id, "sha256": digest}]}
            (root / "bundle" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            issues = check_bundle(root / "bundle")
            assert any("plain file name" in i for i in issues), (bad_id, issues)


def test_a_symlinked_evidence_file_is_rejected():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "bundle").mkdir()
        target = root / "elsewhere.json"
        target.write_text('{"x": 1}', encoding="utf-8")
        try:
            (root / "bundle" / "item.json").symlink_to(target)
        except OSError:
            print("skip  test_a_symlinked_evidence_file_is_rejected (no symlink permission)")
            return
        import hashlib
        manifest = {"providence_version": 1, "generated_at": 1.0, "tool": "test",
                    "items": [{"id": "item", "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}]}
        (root / "bundle" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        issues = check_bundle(root / "bundle")
        assert any("symlink" in i for i in issues), issues


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
