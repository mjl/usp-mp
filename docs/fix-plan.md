# Fix plan for open critique items

Plan for every **[open]** item in [critique.md](critique.md). Item references in brackets point back to it (B = Bugs list number, S = Security, D = Design, P = Packaging, T = Testing).

## Facts checked while planning

These come from the bundled schemas and certificate, not from the live service:

- **Paging** (`zuseaa_p2.xsd`): `Limit` is 1–1000, default 100. `ResultsList` returns at most 1000 `DeliveryID`s plus `HitCount` (the total). `AllDeliveriesType` takes `Paging(Start, Limit)`; `NewDeliveriesOnlyType` takes only `Limit`, so it **cannot be paged**.
- **Checksum** (`zuse_p2.xsd`): `AlgorithmID` is an enumeration of `SHA256` or `SHA512`, and `hashlib.new()` accepts both spellings on 3.11 and 3.14. `Value` is `base64Binary`, which zeep decodes to `bytes`, so comparing it to `digest()` is correct. Most of the *verify* note in B3 is answered; only the dead code remains. Still confirm once against the demo service.
- **Errors**: `QueryDeliveriesResponse` is a `choice` of `ResultsList` or `Error`, and `Error` always has both `ErrorCode` and `ErrorMessage`.
- **`strict=False` cause**: `ContactType` (`zuseaa_p2.xsd:145`) and `zuse_p2.xsd:674` reference the abstract `p:Person`, which `CorporateBody` replaces through an XSD substitution group. zeep's strict parser rejects the substitution.
- **CA certificate**: `CN=BRZ-StammCA-2-02, O=Bundesrechenzentrum GmbH`, valid until **2038-01-12**. Rotation is not urgent, but it should still be documented.

## Verified against the demo service (2026-11-17)

Run with `tests/test_demo.py` and a real client certificate:

- Paging behaves as the schema says: `Start`/`Limit` slices, `HitCount` is the total, and pages past the end are empty. New-only queries are capped by `Limit` (`HitCount` 3, `Limit` 1, one ID). This confirms the 2.3 design.
- The service validates requests (`Limit` 0 or 1001 → `Validation error` fault), and it accepts zeep's `<Query xsi:type=…>` (B11).
- Checksums: `SHA512` on all attachments, `bytes` values, and the declared `Size` matches.
- Unknown UUIDs: `000` / "Unkown uuid." for get, close and delete. Unknown attachment: HTTP 400. Close and delete on known IDs return `Success`; the demo ignores them.
- **4.2 matters more than planned (now done):** with `strict=False`, `Sender`/`Receiver` `CorporateBody` and `AdditionalCriteria` are not parsed but left in `_raw_elements`, so 4.1's `sender`/`receiver` accessors cannot work until 4.2 is solved. With `strict=True`, `GetDelivery` fails on `CorporateBody`.

## Phase 0: Test scaffolding (first, so every later fix lands with a test) — done

Implemented as planned, with two deviations: the fakes (`tests/fakes.py`) return real zeep objects built from the bundled WSDL rather than `SimpleNamespace` trees, and the README documents `uv run --no-project ... --with-editable '.[dev]' pytest` (no lock file or project venv). Known open bugs are captured as `xfail(strict=True)` tests. Writing `test_wsdl.py` surfaced critique B11 (query request shape).

**[T]** Create `tests/` with:

- `conftest.py` with fakes: a fake `service` object whose `QueryDeliveries`, `GetDelivery`, `CloseDelivery` and `DeleteDelivery` return `types.SimpleNamespace` trees shaped like zeep results. A fake adapter fixture patches `USP_MP_Adapter` so no `.p12` or network is needed.
- `test_import.py`: import smoke test for `usp_mp`, `usp_mp.mail` and `usp_mp.cli`. It guards against regressions of B10.
- `test_wsdl.py`: the bundled WSDL loads and resolves `AllDeliveriesType`, `PagingType` and `NewDeliveriesOnlyType`.
- An opt-in integration test, `tests/test_demo.py` with `@pytest.mark.demo`. It is skipped unless `USP_MP_P12` and `USP_MP_P12_PASS` are set, and it runs query, get and download with checksum against the demo endpoint. This is how the remaining *verify* items get confirmed.
- `pyproject.toml`: add `[tool.pytest.ini_options]` with `testpaths = ["tests"]` and register the `demo` marker.
- Document the multi-version run in `README.md`: `uv run --python 3.11 --extra dev pytest` and the same with 3.14.

**Done when:** `pytest` passes on 3.11 and 3.14 with no certificate present.

## Phase 1: Small, low-risk fixes

### 1.1 Attachment download through the session [S2] — done

`adapter.py:121`, `download_attachment`: replace `requests.get(...)` with `self.session.get(..., timeout=self.uspmp.TIMEOUT)`. Drop the explicit `verify`/`cert`, since the session already carries them. That brings the User-Agent, connection reuse and a timeout.
Consider a separate, longer read timeout for large attachments: `timeout=(10, 60)`.
**Test:** mock `session.get`, then assert that the timeout is passed and that `raise_for_status` propagates.

### 1.2 Checksum cleanup [B3, part of B6] — done

`mail.py:84–94`:
- Rename the local `hash` to `hasher`.
- Map `AlgorithmID` through a small dict (`{"SHA256": "sha256", "SHA512": "sha512"}`). An unknown value raises a clear error instead of the dead `None` check.
- Use `hmac.compare_digest` for the comparison. It isn't needed for security, but it is idiomatic.

**Tests:** SHA256 match, SHA512 match, mismatch raises, unknown algorithm raises.

### 1.3 Builtin shadowing [B6] — decided: leave `all=` as is

- `hash` (local): fixed by 1.2.
- `all` (parameter of `Mailbox.deliveries`): this is **public API**, used in the docs and the CLI. The shadowing is local and harmless, so the recommendation is to leave it. If you want it gone, add `new_only: bool = False` and keep `all=` working with a `DeprecationWarning` for one release. **Decision needed** (see the end of this plan).

### 1.4 CLI argument handling [B7, S3] — done (literal password kept, with a warning)

`cli.py`:
- `--client-certificate`: set `required=True`.
- An unset `ENV:NAME` gives `parser.error(f"environment variable {NAME} is not set")` instead of silently prompting.
- A bad `--download` directory gives `parser.error(...)` (clean exit 2, no traceback).
- Wrap the main loop: catch `usp_mp` errors (see 2.3), print one line to stderr, and return 1. `--debug` keeps the full traceback.
- Password on the command line: add `--client-password-file PATH` and print a stderr warning when a literal password is given. Keep the literal form for now (removing it would break scripts). Decided: keep the literal form permanently, with the warning.

**Tests:** argparse-level tests calling `main()` with patched `sys.argv` and asserting `SystemExit` codes.

### 1.5 Filename sanitising [B9, D6] — done

- Move the logic to `src/usp_mp/util.py` as `sanitize_filename(name: str, mime: str) -> str`. `Attachment.sanitized_filename` delegates to it.
- Use `re.sub(r"[^a-z0-9_-]+", "_", stem.lower()).strip("_")`, falling back to `"attachment"` if the result is empty.
- Extension: prefer the original extension when `mimetypes.guess_type(name)` agrees with the declared MIME type. Otherwise use `guess_extension(mime)`, and finally the original extension.
- Collisions: add `unique_path(directory, filename) -> Path`, which appends `_1`, `_2` and so on. Update the `docs/howto.md` example to use it. The CLI already prefixes the index.

**Tests:** table-driven cases (runs of underscores, Unicode, no extension, wrong MIME type, empty stem, collisions).

## Phase 2: Correctness and error model

### 2.1 Exception hierarchy [D5] — done

Implemented with two deviations: `ResultsTruncatedError` was dropped (new-only truncation only warns, per the maintainer's decision, so nothing would raise it), and `MailboxStateError(UspMpError, RuntimeError)` was added for not-open/closed mailboxes. Failures inside `build_ssl_context` also become `CertificateError`.

New `src/usp_mp/errors.py`:

```text
UspMpError(Exception)
├── CertificateError(UspMpError)           # p12 load / password problems
├── ApiError(UspMpError, ValueError)       # .code, .message, .operation
├── ChecksumError(UspMpError, ValueError)
└── ResultsTruncatedError(UspMpError)      # see 2.3
```

`ApiError` and `ChecksumError` also subclass `ValueError`, so existing `except ValueError` code keeps working. `Mailbox.__init__` stops wrapping everything in `RuntimeError`. Instead, `load_p12_cert` raises `CertificateError(...) from e` for a bad password or file, and other exceptions propagate unchanged. Export the classes from `usp_mp/__init__.py`.

### 2.2 `raise_on_error` [B4] — done

```python
def raise_on_error(self, r: Any, operation: str) -> None:
    err = getattr(r, "Error", None)
    if err is not None:
        raise ApiError(err.ErrorCode, err.ErrorMessage, operation)
```

The schema guarantees both fields whenever `Error` is present, so the `in` checks can go. Make it a module-level function, so the `USP_MP_Base` mixin disappears.
**Tests:** response with an error, response without one, response object lacking an `Error` attribute.

### 2.3 Pagination [B2] — done

Implemented as below, with warning-only for new-only truncation (maintainer decision; no drain mode). The live check found critique B12: on the demo, new-only `Limit` applies before the unread filter. The warning still fires because it compares against `HitCount`.

`Mailbox.deliveries()`:
- **All deliveries:** loop `Start = 0, 1000, 2000…` with `Limit=1000` until the collected count reaches `HitCount` or a page comes back empty. **Collect all IDs before yielding.** Otherwise a caller that closes or deletes during iteration shifts the server-side offsets and skips messages.
- **New only:** cannot be paged. Request `Limit=1000`. If `HitCount > len(ids)`, log a warning (see 4.3) and document that the caller should close the processed deliveries and query again. Optionally, add a `drain=True` mode that repeats the query while the batch is full and the deliveries are being closed.
- Expose `limit: int | None = None` for callers who want fewer.
- Update `docs/howto.md`, which currently says "capped at 100… not yet implemented".

**Tests:** fake service returning 2,500 IDs over three pages; `HitCount=0`; an inconsistent `HitCount` (larger than what is ever returned) stops on an empty page; new-only truncation logs a warning.

### 2.4 `--save` directory naming [B8] — done

`cli.py`: `save_path = dest / delivery.id` with `mkdir(exist_ok=True)`. Write to `name.part` and rename on completion, so a crash never leaves a half-written file that looks finished. Print the index for display only.
This changes the on-disk layout, so add a CHANGELOG entry.

## Phase 3: Connection and key lifecycle

These items are coupled; do them together in one PR.

### 3.1 Private key handling [S1, B5] — done (encrypted temp key)

Python's `ssl` module has no in-memory key loading: `SSLContext.load_cert_chain(certfile, keyfile=None, password=None)` takes paths only (checked on 3.11 and 3.14). The two workable ways to avoid a plaintext key file both passed a mutual-TLS test against a local server on macOS with Python 3.11 and 3.14, using `requests` with a custom `HTTPAdapter`:

- **Encrypted temp key (recommended).** Re-serialise the key with `serialization.BestAvailableEncryption(secrets.token_bytes(32))`, write it to a `TemporaryDirectory`, and call `load_cert_chain(cert, key, password=pw)`. Only ciphertext touches the disk, the one-time password exists only in memory, and a file left behind by a crash is useless. Cross-platform, with no threads.
- **Named pipe (FIFO).** `os.mkfifo` in a private temp dir, with a writer thread streaming the plaintext PEM while OpenSSL reads it. No key bytes are ever stored, but it's POSIX-only and needs a thread. If loading fails before OpenSSL opens the pipe, the writer blocks on `open()`, so it needs non-blocking handling. Not worth the fragility.
- Rejected: `memfd_create` (Linux-only, the maintainer uses macOS), and pyOpenSSL (extra dependency, plus urllib3's pyOpenSSL support is legacy).

Implementation, done once in `USP_MP_Adapter.__init__`:

1. Create the context with `urllib3.util.ssl_.create_urllib3_context()` and call `load_verify_locations(CA_PATH)`. This matches what `requests` does today, including `VERIFY_X509_STRICT` with urllib3 2.7 on 3.14. The BRZ root has the SKI/AKI extensions that strict mode requires.
2. Inside a `tempfile.TemporaryDirectory()`, write the cert PEM and the **encrypted** key, then call `load_cert_chain(cert, key, password=pw)`. The directory is removed on exit, even on error.
3. Mount a small `HTTPAdapter` subclass on the session that passes `ssl_context=ctx` to `init_poolmanager`.
4. Don't keep `key_pem` (or the decrypted key object) on the adapter afterwards; currently `self.key_pem` holds the plaintext PEM for the adapter's lifetime.

The `ConnectionContext` temp-file code (`__enter__`/`__exit__`) then disappears. A crash during loading leaves at most an encrypted key in a temp dir.

Check during implementation: when `session.verify` is a path, requests also passes `ca_certs` to urllib3, which loads it into the same context. That's harmless, but set `session.verify = CA_PATH` explicitly, so that removing the adapter can never fall back to the system trust store. Also test that a server with a cert outside the BRZ CA is rejected.

**Tests:** after adapter construction no temp files remain (patch `tempfile.mkdtemp` to a known dir); a failure inside `load_cert_chain` still removes the directory.

### 3.2 Build the zeep client once [D1, D2, D3] — done

**Remainder done:** the adapter parses the WSDL once into a zeep `Document` (parsed with the `strict=False` settings, because zeep reads `strict` from the Document's settings), and each connection creates a lightweight `Client(wsdl=document, transport=…)` around its own session. `types` is a per-adapter frozen `QueryTypes` dataclass; the class-level `ParamTypes` is gone. Thread safety is documented in the class docstring and the howto.

**Done (decision (b), 2026-11-17):** `Mailbox` is a mandatory context manager (`with Mailbox(...) as mbox:`) that owns one connection, so the SOAP client is built once per mailbox. `ConnectionContext.service` is a property that is always set and raises when closed, and the asserts are gone. Since the CLI was the only caller, breaking the API was acceptable. **Still open:** the bullets on dropping the separate `read_wsdl_types` parse and making `types` per instance, plus the thread-safety note.

- Move `requests.Session`, `Transport`, `Client` and `create_service` into `USP_MP_Adapter.__init__`, and drop the separate `read_wsdl_types` parse. Resolve the three types from the same client.
- Make `types` an **instance** attribute: a small frozen dataclass assigned in `__init__`. Remove the class-level `ParamTypes()` instance.
- `service` is always set, so remove `self.service = None`, `if not conn.service` and every `assert connection.service`.
- ~~Keep `connection()`, or make `Mailbox` a context manager?~~ Decided: `Mailbox` as a **mandatory** context manager (option (b)); `connection()` remains as the building block underneath.
- Document that an adapter is not thread-safe (one per thread).

### 3.3 N+1 round trips [B5] — done (documented)

This is inherent to the API: one `GetDelivery` per ID. After 2.3, the IDs are fetched eagerly and each `Delivery` is fetched lazily on iteration. That's acceptable, so just document it. If it matters later, `Delivery` could defer `GetDelivery` until the first property access.

## Phase 4: API surface and diagnostics

### 4.1 Typed accessors [D4] — done

Implemented as planned, with `Contact` as a frozen dataclass (`kind`, `name`, `organization`, `given_name`, `family_name`, `date_of_birth`, `identification_type`, `identification_value`). `Delivery.delivery` and `Attachment.attachment` stay (no deprecation, since there are no external callers), next to the new `raw`.

On `Delivery`, add properties for the useful `MetaData` fields: `app_delivery_id`, `zs_delivery_id`, `gz` (Geschäftszahl), `origin` and `delivery_quality`. Also add `sender` and `receiver`, returning a small dataclass (`name`, `kind` set to "person" or "corporate body", and `identification`). Wrapping the zeep `Delivery.delivery` and `Attachment.attachment` objects, keep both reachable as `.raw` for advanced use, and keep the old attribute names as aliases for one release.
Check the actual field shapes against demo data first, because the `Person`/`CorporateBody` substitution (see 4.2) affects how `sender` parses.

### 4.2 `strict=False` [S4] — done

**Result:** step 1 (the schema patch) worked; all demo responses parse with `strict=True`. Step 2 was decided differently from the plan: the library **keeps `strict=False`**, so an unknown element in a future response can't block mail retrieval. Drift becomes visible instead: `Delivery` logs a warning listing unparsed elements (`usp_mp.mail.unparsed_elements`), and the demo suite parses every live response with `strict=True`. Note that zeep also leaves everything after an unknown element in the same sequence unparsed. 4.1 (sender/receiver accessors) is unblocked.

Confirmed on the demo service (see above): sender and receiver organisation data is currently lost, and the two `xfail` tests in `tests/test_demo.py` will flag when this is fixed. Do 4.2 before 4.1.

1. Try a local schema patch: at `zuseaa_p2.xsd:145` and `zuse_p2.xsd:674`, replace `<xs:element ref="p:Person"/>` with `<xs:choice><xs:element ref="p:PhysicalPerson"/><xs:element ref="p:CorporateBody"/></xs:choice>`. Keep a `wsdl/PATCHES.md` that records the change and the reason, since the files are vendor copies.
2. Run the demo integration test with `strict=True`. If it passes, switch to strict mode.
3. If zeep still fails, keep `strict=False` but replace the "I don't really care…" comment with the precise reason and a link to this plan, and add a test asserting the fields we rely on (`DeliveryID`, `MetaData.Subject`, `AttachmentList`, `CheckSum`) are present, so a silently mis-parsed response fails loudly.

### 4.3 Library logging [P5] — done

As planned, plus a WARNING for a client certificate expiring within 30 days (or expired) and `adapter.certificate_expires`. The CLI's `--debug` setup no longer uses `dictConfig`, which disabled existing loggers. Completes Phase 4.

- Add `logger = logging.getLogger(__name__)` in `adapter.py` and `mail.py`. Log at DEBUG: endpoint selection, query parameters and `HitCount`, page fetches, and attachment downloads (ID and size). Log at WARNING: truncated new-only results (2.3). **Never** log passwords, PEM data or attachment content.
- `cli.py --debug` configures both the `usp_mp` and the `zeep.transports` loggers.

## Phase 5: Packaging, licence and docs

### 5.1 Project description [P1] — done (without `[project.urls]`: the repository is private)

`pyproject.toml`: `description = "Client for the Austrian USP 'Mein Postkorb' automatic retrieval (Automatische Abholung) service"`. Also add `keywords`, `classifiers` (Python 3.11–3.14, OS-independent) and `[project.urls]` if the repository should be public.

### 5.2 Licence [P2] — done: BSD-3-Clause

Decided: BSD 3-Clause. Originally considered: MIT (simplest), Apache-2.0 (patent grant), or EUPL-1.2 (common for Austrian/EU public-sector-adjacent software). Then add `LICENSE` and set `license = "<SPDX id>"` plus `license-files = ["LICENSE"]` in `pyproject.toml`. That PEP 639 form needs `setuptools>=77`, so bump `build-system.requires` to match.

### 5.3 CA certificate documentation [P3] — done, except the source

Add a "Trust anchor" section to `docs/howto.md` with the subject, issuer, SHA-256 fingerprint and expiry (2038-01-12), where it was obtained, and how to refresh it. Include the inspection command:

```bash
openssl x509 -in src/usp_mp/certs/brz_ca.cer -noout -subject -issuer -enddate -fingerprint -sha256
```

Add a test that **warns** (does not fail) when the certificate expires within a year. A failing date-based test would be a time bomb.
**Open question:** the source of `brz_ca.cer` isn't recorded anywhere in the repo; fill it in when known.

### 5.4 Versioning [P4] — decided: keep the current scheme

Decided: keep `1.0.YYYYMMDD`, bumped by hand. Options that were considered:
- (a) Keep date-based versions, but use PEP 440 CalVer `2026.11.17` instead of the `1.0.` prefix. It needs a manual bump, and the `1.0.x` → `2026.x` change sorts correctly (higher).
- (b) Semantic versioning (`1.1.0`, …), single-sourced from git tags via `setuptools-scm`.

The recommendation is (b). The breaking changes listed in the CHANGELOG (CLI script removal, `--save` layout) are exactly what semver communicates. Whichever you choose, start tagging releases and rename the CHANGELOG's *Unreleased* heading when you tag.

### 5.5 Vendor documents in `docs/` [P6]

**Decision needed.** The README already links the official HowTo. Options:
- Keep the files and add a note on their origin and that they are reproduced for reference.
- Remove the PDF, `.txt` and zip from HEAD and rely on the link. They stay in git history; purging history needs a rewrite and force-push, which is not recommended for this alone.

~~Also exclude `docs/api_docs/` from any sdist.~~ Not needed: a test build showed setuptools does not put `docs/` into the sdist at all.

## Suggested PR sequence

| # | PR | Items | Breaking? |
|---|----|-------|-----------|
| 1 | Test scaffolding | T | no |
| 2 | Quick fixes | 1.1, 1.2, 1.3, 1.5 | no |
| 3 | CLI hardening | 1.4, 2.4 | `--save` layout changes |
| 4 | Error model + pagination | 2.1, 2.2, 2.3 | no (`ValueError` compatible) |
| 5 | Connection/key refactor | 3.1, 3.2, 3.3 | internal; `ConnectionContext` changes |
| 6 | Diagnostics + accessors | 4.1, 4.2, 4.3 | no (aliases kept) |
| 7 | Packaging/docs | 5.1–5.5 | no |

After each PR, update `critique.md` status markers and `CHANGELOG.md`.

## Decisions needed from the maintainer

1. ~~Keep `deliveries(all=...)`, or rename it to `new_only` with a deprecation (1.3)?~~ Decided: keep it.
2. ~~Eventually remove the literal `--client-password` value (1.4)?~~ Decided: keep it, with the warning.
3. ~~Keep `connection()`, or move to `Mailbox` as a context manager (3.2)?~~ Decided: mandatory `with Mailbox(...) as mbox:`.
4. ~~Which licence (5.2)?~~ Decided: BSD 3-Clause.
5. ~~Which versioning scheme (5.4)?~~ Decided: keep `1.0.YYYYMMDD`.
6. Keep or remove the vendor documents (5.5)?
7. Where did `brz_ca.cer` come from (5.3)?
