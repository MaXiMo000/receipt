# Providence bundle spec, v1

A Providence bundle proves "this content existed at this time and hasn't
been edited since" -- nothing more. Not a signature scheme, not an audit
log, not a database. A directory or file plus a sha256, same as `receipt`'s
and `invariant`'s own doc comments already put it.

## Why this exists

`receipt` and `invariant` both already write evidence to disk as
content-plus-hash. They independently arrived at two different envelope
shapes for the same idea:

- `receipt` writes **one file**: `{schema_version, receipt: <result>,
  sha256, written_at}`, where `sha256` covers `receipt` under
  `json.dumps(result, indent=2, sort_keys=True, default=str)`.
- `invariant` writes **a directory**: `manifest.json` (`{generated_at,
  checks: [{name, status, detail, seconds, sha256}]}`) plus one
  `<name>.json` file per check, each check's `sha256` covering that file's
  exact bytes.

Nothing was wrong with either -- but a third tool wanting to read "did this
evidence get tampered with" has to special-case both shapes, and a fourth
tool adding a third shape makes it worse. Providence names the one pattern
underneath both and gives it a single, versioned, checkable form.

## The two conformant forms

### Directory form (matches `invariant` today, field names aside)

```
bundle/
  manifest.json
  <id-1>.json
  <id-2>.json
  ...
```

`manifest.json`:

```json
{
  "providence_version": 1,
  "generated_at": 1757430000.0,
  "tool": "invariant",
  "items": [
    { "id": "no_negative_payments", "sha256": "<64 hex chars>", "status": "pass", "detail": "" }
  ]
}
```

Each `items[].sha256` is the sha256 of the **exact on-disk bytes** of
`<id>.json`. `status` and `detail` are optional and tool-defined --
Providence doesn't standardize what a check result looks like, only how
it's proven not to have been silently edited.

`id` must be a non-empty string usable as a plain file name: no `/`, `\`,
`:` or NUL, and not `.` or `..`. A checker must reject any other id rather
than resolve it, or a manifest could vouch for a file outside the bundle.

### Single-file form (matches `receipt` today, field names aside)

```json
{
  "providence_version": 1,
  "generated_at": 1757430000.0,
  "tool": "receipt",
  "payload": { "...": "tool-defined result" },
  "sha256": "<64 hex chars>"
}
```

`sha256` is the sha256 of `payload`'s **canonical serialization**:
`json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)`,
UTF-8 encoded. Canonical, not "however the writer happened to serialize
it," so two independent implementations hash the same payload to the same
digest without needing to agree on indentation.

Full JSON Schema for the directory form's `manifest.json`:
[`schema/manifest.schema.json`](schema/manifest.schema.json). The
single-file form doesn't get a separate schema file -- it's the same four
envelope fields plus `payload`/`sha256` instead of `items`, small enough
to state inline above.

## What conformance checking actually verifies

`providence check <bundle>`:

1. The envelope has `providence_version` (== 1), `generated_at`, `tool`.
2. Directory form: every `items[]` entry has a matching `<id>.json` file,
   and that file's real sha256 matches what the manifest claims. Any file
   on disk *not* referenced by the manifest is flagged too (not
   necessarily tampering, but the bundle claims to be self-describing).
3. Single-file form: `payload`'s canonical sha256 matches `sha256`.

A hash mismatch means the content changed after the manifest was written,
or the manifest was hand-edited to lie about what it covers. Either way,
conformance checking is what catches it -- that's the entire point.

## Redaction and secrets

Providence proves a bundle wasn't silently edited after it was written. It
says nothing about whether it was safe to write in the first place --
`generated_at`/`tool`/`items[].sha256` (or `payload`/`sha256`) are the only
fields conformance checking ever looks at, and it never opens `payload` or
an `<id>.json` file's *content* except to hash it. A payload holding a raw
secret hashes and validates exactly as cleanly as one that's been
redacted; Providence has no way to tell the difference, and isn't trying
to.

That responsibility sits with whatever tool builds the payload, before it
ever reaches Providence's envelope -- and every one of this bundle
shape's two source tools has already had to learn that the hard way, not
hypothetically: `receipt` ships `redact.py` (a real credential a wrapped
command echoed to stdout landed verbatim in a receipt on disk, the first
time, before it existed) and `invariant`'s `sql` check type redacts a DSN's
password before it's returned as evidence. Both bugs were found *after*
each tool already had working conformant output -- proof that "the bundle
validates" and "the bundle is safe to hand to someone else" are two
different claims, and only the first one is what `providence check`
actually makes.

A tool building a Providence bundle should redact secret-shaped content
in its `payload` (or an `items[]` entry's file) **before** computing the
sha256 that goes into the envelope, the same way `receipt` and `invariant`
now do -- redacting after hashing would just make the hash lie about what
the bundle currently contains. This is a requirement on the producer, not
something a future version of Providence should try to enforce centrally:
a generic content-shaped secret scanner is a different, much bigger tool
than an evidence-envelope spec, and bolting one on here would blur exactly
the boundary this spec exists to keep clean (see "Status," below).

## Status: naming, not (yet) building

`receipt` and `invariant` are not required to change what they write.
`providence convert-invariant` / `providence convert-receipt` read each
tool's real, current output and re-emit it in Providence's canonical form
-- a converter, not a migration. Both tools writing this shape directly,
instead of a converter reading their own shape after the fact, is real
future work and explicitly not done here.

`carabiner`'s `Finding` schema and `firedrill`'s (unversioned) `Finding`
dataclass are a *related but different* pattern -- a versioned shape for
one item, not a content-hashed bundle on disk. A `Finding` could
reasonably become the `payload` of a Providence single-file bundle, or one
`items[]` entry's evidence in a directory bundle, but that unification
isn't done here either. Naming that relationship without forcing it is
deliberate, not an oversight -- see the parent portfolio's `HANDOFF.md` §7.
