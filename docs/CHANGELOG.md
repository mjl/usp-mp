# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/). The repository has no release tags; versions are the value of `usp_mp.__version__` at the time, and dates come from the git history.

## [Unreleased]

Changes since `1.0.20260224` (the version number has not been bumped; commits date from 2026-04-16 to 2026-11-17).

### Added

- Demo script `--save` option: saves each delivery's attachments into a numbered directory and marks the delivery as read only after saving.
- `uspmp` console script (`[project.scripts]`), installed with the package. The CLI now lives in `src/usp_mp/cli.py`.
- Installation instructions in `README.md`.
- `docs/critique.md`: project review with status of each finding.
- `docs/fix-plan.md`: phased plan for the open critique items.
- Test suite (run with `pytest`; see README "Development"): an in-memory fake of the service built from the bundled WSDL; tests for `Mailbox`/`Delivery`/`Attachment`, the CLI, the WSDL and request XML, imports, and client-certificate TLS; strict `xfail` tests for known open bugs; and opt-in, read-only tests against the demo service (`pytest -m demo` with `USP_MP_P12`/`USP_MP_P12_PASS`).

### Changed

- Clearer password prompt in the CLI (names the certificate file).
- `howto.md` moved into `docs/`.
- **The demo script `uspmp.py` is gone.** Use the `uspmp` command instead of `python uspmp.py`.
- `requires-python` lowered from `>=3.14` to `>=3.11`.
- `.gitignore` rewritten: dropped the blanket `.*` rule and unused template sections, added `.jj/`, and the bundled CA certificate is explicitly un-ignored.
- Documentation wording and spelling fixes (service and portal names, "Postbevollmächtigter", various typos).

### Security

- The client private key is no longer written to disk in plaintext for the duration of a connection. The adapter builds one `SSLContext` at construction; the key touches the disk only encrypted with a random one-time password, in a private temp directory that is removed immediately after loading (also on error). `USP_MP_Adapter` no longer has `cert_pem`/`key_pem` attributes or `read_cert()`; it has `ssl_context` and `build_ssl_context()` instead.

### Fixed

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
