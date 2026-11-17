# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/). The repository has no release tags; versions are the value of `usp_mp.__version__` at the time, and dates come from the git history.

## [Unreleased]

### Added

- Demo-service test suite (`tests/test_demo.py`, marker `demo`): 36 tests against the Mein Postkorb TEST endpoint plus 2 strict `xfail` tests for the known `CorporateBody` parsing gap. Besides invariants, they compare the fixed demo data against the values documented in the vendor HowTo and against a golden snapshot (`tests/data/demo_snapshot.json`, re-recorded with `--update-demo-snapshot`). Each person running them provides their own client certificate and password via `--p12=` / `--p12-password-file=` / `--p12-password=` or `USP_MP_P12` / `USP_MP_P12_PASS_FILE` / `USP_MP_P12_PASS`; without a certificate they are skipped. See README "Tests against the demo service".

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
