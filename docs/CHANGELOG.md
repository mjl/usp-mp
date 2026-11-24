# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/). The repository has no release tags; versions are the value of `usp_mp.__version__` at the time, and dates come from the git history.

## [Unreleased]

### Removed

- The BRZ How-To PDF, its text extraction, and the example-project zip (BRZ copyright); `docs/api_docs/README.md` now links to the sources.
- `docs/critique.md` and `docs/fix-plan.md` (internal working notes). Code comments that cited their item numbers now explain the behaviour themselves.
- Stale notes about `xfail` tests (none are left).

### Fixed

- Packaging: setuptools warned that `usp_mp.certs` and `usp_mp.wsdl` were missing from the `packages` configuration, and that a future version might drop their data files from the wheel.

## [1.3.20261117] - 2026-11-17

Changes since `1.2.20261117`.

**Possibly breaking:** the higher dependency floors (`zeep>=4.3.3`, `cryptography>=48.0.1`, `lxml>=6.1.1`) can conflict with environments pinned to older versions.

### Changed

- Dependency floors: `zeep>=4.3.3` (was `>=4.2`, which fails on Python 3.13+ and lacks the fix for GHSA-4cc2-g9w2-fhf6), `cryptography>=48.0.1` (was `>=41`; fixes the vulnerable OpenSSL in the wheels, GHSA-537c-gmf6-5ccf), and `lxml>=6.1.1`, now declared because `mail.py` imports it (CVE-2026-41066). The tests pass with exactly these versions, together with `requests` 2.32.4 and `urllib3` 2.8.0, on Python 3.11 and 3.14.
- Removed the unused `pytest-mock` from the `dev` extra.

## [1.2.20261117] - 2026-11-17

Changes since `1.1.20261117`.

**Possibly breaking:** the new dependency floors (`requests>=2.32.4`, `urllib3>=2.8.0`) can conflict with environments pinned to older versions.

### Added

- `uspmp --delete` (requires `--save`): deletes each delivery from the mailbox once it and all its attachments are downloaded, checksum-verified, written, flushed to disk and read back unchanged. Any failure keeps the delivery.
- `usp_mp.util.fsync_dir(directory)`.

### Changed

- `uspmp --save` now flushes and reads back the saved files before marking a delivery as read.
- Dependencies: `requests>=2.32.4` (was `>=2`) and `urllib3>=2.8.0`, now declared explicitly since the adapter imports it. Both floors pick up security fixes (CVE-2024-47081, GHSA-8988-9cw3-xx77 and the urllib3 decompression-bomb fixes); the suite passes with exactly these versions.
- The warning for truncated new-only results now explains the cause (the service searches only the first 1000 deliveries of the whole mailbox for unread ones, on demo and production alike) and the remedies: delete processed deliveries, since closing is not enough, or use `deliveries(all=True)` and track processed IDs. The previous text wrongly advised closing deliveries and querying again. The howto documents the limitation.

## [1.1.20261117] - 2026-11-17

Changes since `1.0.20261117`: Phases 0–5 of the fix plan (error types, pagination, the `p:Person` schema patch, typed accessors, logging, packaging, and documentation), plus the demo-service test suite.

**Breaking changes** (the version scheme doesn't signal them, so they are listed here):

- `uspmp --save` writes one directory per delivery ID instead of `0/`, `1/`, ….
- Certificate problems raise `usp_mp.CertificateError`, which is not a `RuntimeError`. API, checksum and mailbox-state errors are new classes but still match `ValueError`/`RuntimeError`. Error messages changed (for example `GetDelivery failed: [000] Unkown uuid.`).
- `Mailbox.deliveries()` sends its query when called, not on first iteration. `all=False` now returns up to 1000 deliveries (previously 100).
- `USP_MP_Adapter.read_wsdl_types()` is replaced by `load_wsdl()`, and `adapter.types` is a per-instance `QueryTypes` (no longer class-level).
- The CLI's output changed: each listing line ends with `… from <sender>`, and library warnings appear on stderr as `uspmp: warning: …`.

### Added

- `docs/api_docs/README.md`: origin and licence status of the BRZ documents in `docs/`. `src/usp_mp/wsdl/PATCHES.md` also records the flattened schema paths.
- Documentation of the bundled BRZ root CA ("Trust anchor" in `docs/howto.md`) and `tests/test_ca.py`, which pins its fingerprint and warns a year before it expires.
- Logging under `usp_mp` (DEBUG: endpoint, connections, queries, fetches, downloads; INFO: close/delete; WARNING: truncated new-only results, unparsed response elements, a client certificate expiring within 30 days or expired). `USP_MP_Adapter.certificate_expires`.
- Typed accessors: `Delivery.sender` and `Delivery.receiver` (a `Contact`: organisation or natural person, with identification), `Delivery.gz`, `delivery_quality`, `app_delivery_id`, `zs_delivery_id`, `delivery_service`, `origin` and `raw`; `Attachment.size`, `document_class` and `raw`.
- `usp_mp.mail.unparsed_elements(value)` lists XML elements zeep couldn't map to the schema; `Delivery` logs a warning on `usp_mp.mail` when a response contains any.
- `usp_mp.util.write_atomic(path, data)`.
- `Mailbox.deliveries(..., limit=N)` caps the number of deliveries returned.
- Exception hierarchy in `usp_mp.errors`, exported from `usp_mp`: `UspMpError`, `CertificateError`, `ApiError` (`.code`, `.message`, `.operation`), `ChecksumError` and `MailboxStateError`. `Mailbox` is now exported from `usp_mp` too.
- Demo-service test suite (`tests/test_demo.py`, marker `demo`): tests against the Mein Postkorb TEST endpoint, including strict parsing of every live response as a schema-drift check. Besides invariants, they compare the fixed demo data against the values documented in the vendor HowTo and against a golden snapshot (`tests/data/demo_snapshot.json`, re-recorded with `--update-demo-snapshot`). Each person running them provides their own client certificate and password via `--p12=` / `--p12-password-file=` / `--p12-password=` or `USP_MP_P12` / `USP_MP_P12_PASS_FILE` / `USP_MP_P12_PASS`; without a certificate they are skipped. See README "Tests against the demo service".

### Changed

- Package metadata: a real description instead of the template placeholder, plus keywords and classifiers.
- CLI: library warnings are shown as `uspmp: warning: …`; `--debug` adds the library's debug output to the SOAP traces.
- The `uspmp` listing shows each delivery's sender (`… from Bundeskanzleramt`).
- `USP_MP_Adapter` parses the WSDL once per adapter (`load_wsdl()`, replacing `read_wsdl_types()`) and its connections reuse it. `adapter.types` is now a per-adapter frozen `QueryTypes` instead of a class-level object shared by all adapters. The `strict=False` workaround is documented with its cause and side effect.
- **`uspmp --save` layout:** each delivery is saved into a directory named by its delivery ID instead of its position in the listing (`0/`, `1/`, …). Files are written atomically via a `.part` file.
- `Mailbox.deliveries()` queries the delivery IDs when called (previously on first iteration); each delivery is still fetched lazily.
- Error types and messages: certificate problems raise `CertificateError` (previously `RuntimeError("Failed to initialize USP_MP_Adapter: …")`, or `ssl.SSLError`/`ValueError` from the adapter); API errors raise `ApiError` with messages like `GetDelivery failed: [000] Unkown uuid.`; checksum problems raise `ChecksumError`; using a closed or unopened mailbox raises `MailboxStateError`. The previous builtin types still match (`ApiError` and `ChecksumError` are `ValueError`s, `MailboxStateError` is a `RuntimeError`), except for certificate errors. The CLI prefixes unexpected (non-`usp_mp`) errors with their type.

### Fixed

- The sdist now contains the complete test suite (previously only `tests/test_*.py`, without `conftest.py`, the fakes and test data).
- Sender and receiver details (`CorporateBody`, and everything after it in `Sender`/`Receiver`) were silently left unparsed, because zeep doesn't support the `p:Person` substitution group. The bundled schema is patched with an equivalent explicit choice (`src/usp_mp/wsdl/PATCHES.md`); responses now parse completely.
- A second `uspmp --save` run into the same directory failed because the numbered directories already existed, and numbers referred to different deliveries from run to run.
- `Mailbox.deliveries(all=True)` returned at most 100 deliveries; it now pages through the whole mailbox (1000 per request). All IDs are collected before iterating, so closing or deleting during the loop can't skip deliveries. `all=False` returns up to 1000 (previously 100) and logs a warning on `usp_mp.mail` when the service reports more.

## [1.0.20261117] - 2026-11-17

Changes since `1.0.20260224` (commits from 2026-04-16 to 2026-11-17).

**Breaking changes** (the version scheme doesn't signal them, so they are listed here):

- `Mailbox` must be used as a context manager: `with Mailbox(...) as mbox:`. `deliveries()` outside the block raises `RuntimeError`.
- The `uspmp.py` script is replaced by the installed `uspmp` command.
- `Attachment.sanitized_filename` produces different names.
- `USP_MP_Adapter` lost `cert_pem`, `key_pem` and `read_cert()`.
- The CLI exits with status 1 and a one-line message on errors, and 2 on invalid arguments, instead of raising.

### Added

- `Mailbox` context manager support (`__enter__`/`__exit__`), `Mailbox.close()`, and `Mailbox.service` for raw SOAP calls on the open connection.
- BSD 3-Clause licence (`LICENSE`, declared in the package metadata).
- `usp_mp.util` with `sanitize_filename(name, mime)` and `unique_path(directory, filename)`.
- CLI option `--client-password-file PATH`.
- Demo script `--save` option: saves each delivery's attachments into a numbered directory and marks the delivery as read only after saving.
- `uspmp` console script (`[project.scripts]`), installed with the package. The CLI now lives in `src/usp_mp/cli.py`.
- Installation instructions in `README.md`.
- `docs/critique.md`: project review with status of each finding.
- `docs/fix-plan.md`: phased plan for the open critique items.
- Test suite (run with `pytest`; see README "Development"): an in-memory fake of the service built from the bundled WSDL; tests for `Mailbox`/`Delivery`/`Attachment`, the CLI, the WSDL and request XML, imports, and client-certificate TLS; strict `xfail` tests for known open bugs; and opt-in, read-only tests against the demo service (`pytest -m demo` with `USP_MP_P12`/`USP_MP_P12_PASS`).

### Changed

- **`Mailbox` must be used as a context manager.** It owns one connection (HTTP session and zeep client) for the whole `with` block instead of opening one per `deliveries()` call, and closes it deterministically, also on exceptions or an early `break`. Outside the block, `deliveries()` and anything that contacts the service (`Attachment.content()`, `Delivery.close()`/`delete()`) raise `RuntimeError`; already-fetched data stays readable. A `Mailbox` can be opened only once. `USP_MP_Adapter.ConnectionContext` gained `closed` and `close()`, and its `service` raises once closed.
- `Attachment.sanitized_filename` produces different, cleaner names: runs of unsafe characters (including underscores) collapse to one `_`, accents fold to ASCII (`März` → `marz`), the stem is capped at 100 characters, and a matching original extension is kept (`scan.jpeg` stays `.jpeg`). Code that relies on the old names, such as previously saved files, sees new names.
- CLI: `--client-certificate` is required and must exist; invalid arguments (missing `ENV:` variable, bad `--download` directory) are usage errors with exit status 2. Runtime errors print a single `uspmp: error: …` line and exit with status 1 instead of a traceback (`--debug` shows it). A non-empty literal `--client-password` prints a warning.
- Clearer password prompt in the CLI (names the certificate file).
- `howto.md` moved into `docs/`.
- **The demo script `uspmp.py` is gone.** Use the `uspmp` command instead of `python uspmp.py`.
- `requires-python` lowered from `>=3.14` to `>=3.11`.
- `.gitignore` rewritten: dropped the blanket `.*` rule and unused template sections, added `.jj/`, and the bundled CA certificate is explicitly un-ignored.
- Documentation wording and spelling fixes (service and portal names, "Postbevollmächtigter", various typos).

### Security

- The client private key is no longer written to disk in plaintext for the duration of a connection. The adapter builds one `SSLContext` at construction; the key touches the disk only encrypted with a random one-time password, in a private temp directory that is removed immediately after loading (also on error). `USP_MP_Adapter` no longer has `cert_pem`/`key_pem` attributes or `read_cert()`; it has `ssl_context` and `build_ssl_context()` instead.

### Fixed

- Checksum verification: unsupported `AlgorithmID` values now raise a clear `ValueError` naming the algorithm (previously a generic `hashlib` error, behind an unreachable `None` check). Digests are compared with `hmac.compare_digest`.
- Attachment downloads now go through the session: a timeout applies (previously none), and the `USP_MP/<version>` User-Agent and connection reuse are used.
- `import usp_mp.mail` raised `NameError` on Python before 3.14 (forward reference to `Delivery` in an annotation); added `from __future__ import annotations`.
- CLI exit status: `main()`'s return value is now actually used (`raise SystemExit(main())`) instead of being discarded.

## [1.0.20260224] - 2026-02-24

### Added

- Vendor documentation under `docs/`: the original *Automatische Abholung* HowTo (PDF and text) and the example project archive.
- Expanded `howto.md` covering prerequisites, certificate setup, CLI and library usage.

### Changed

- Error checking for `QueryDeliveries`, `GetDelivery`, `CloseDelivery` and `DeleteDelivery`: responses carrying an error now raise `ValueError` with the API error code and message.
- Version bumped from `1.0.20260216`.

## [1.0.20260216] - 2026-02-16

### Added

- Initial `usp_mp` package: `USP_MP_Adapter` (PKCS#12 client certificate loading, zeep client from the bundled WSDL, BRZ CA pinning, demo and production endpoints).
- Higher-level API in `usp_mp.mail`: `Mailbox`, `Delivery` and `Attachment`, with checksum-verified attachment download, `close()` and `delete()`.
- Demo command line script and a minimal README.
