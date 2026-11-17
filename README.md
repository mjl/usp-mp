


# USP Mein Postkorb

This package provides access to the *Mein Postkorb* service of the Austrian Government's *Unternehmensserviceportal* (see references below). 
You can retrieve new or all deliveries (Nachrichten), mark them as read or delete them, and download attachments.

# Installation

Requires Python 3.11 or newer. The package is not published on PyPI; install it from a checkout:

```bash
git clone <repository-url> usp-mp
cd usp-mp
python -m venv .venv && . .venv/bin/activate
pip install .
```

This installs the `usp_mp` library and the `uspmp` command line tool:

```bash
uspmp --help
```

To install only the command line tool, use `pipx install .` or `uv tool install .` instead. For development, add `-e` and the test extras: `pip install -e '.[dev]'`.

You also need a client certificate (`.p12`) for the *Automatische Abholung*; see [docs/howto.md](docs/howto.md) for how to obtain one and for usage.

# Development

Run the tests (no certificate or network needed; a fake service stands in for Mein Postkorb):

```bash
pip install -e '.[dev]'
pytest
```

Or, with [uv](https://docs.astral.sh/uv/), on a specific Python version. Run at least the oldest (3.11) and the newest supported version:

```bash
uv run --no-project --python 3.11 --with-editable '.[dev]' pytest
uv run --no-project --python 3.14 --with-editable '.[dev]' pytest
```

Tests marked `xfail` document known open issues from [docs/critique.md](docs/critique.md). They run in strict mode, so when a fix makes one pass, pytest reports it as a failure until its `xfail` marker is removed.

## Tests against the demo service

`tests/test_demo.py` runs against the Mein Postkorb **demo (TEST) endpoint**. It covers TLS with the client certificate, querying and paging, metadata, downloads with checksum verification, the documented error responses, close/delete, and the `uspmp` CLI.

The demo always returns the same mock data, and the tests check it against expected results in three layers:

1. **Invariants** that hold for any data: `HitCount` matches, paging reassembles the full list, checksums verify, and so on.
2. **Values documented in the vendor HowTo:** the 10 delivery IDs in order, the 3 new ones, and the attachment-download example.
3. **A golden snapshot** of everything else in [tests/data/demo_snapshot.json](tests/data/demo_snapshot.json): subjects, timestamps, and attachment IDs, names, MIME types, sizes and checksums. A mismatch lists each difference by path. If BRZ refreshes the demo data, re-record it with `--update-demo-snapshot` (plus the certificate options) and review the diff before committing.

**You must bring your own client certificate and password.** Neither is part of this repository, and no test contains or defaults to one. Each person running these tests uses a `.p12` issued for their own USP account (see the certificate setup checklist in [docs/howto.md](docs/howto.md)). Without a certificate, the demo tests are skipped and everything else runs normally.

Provide the certificate and password with pytest options or environment variables (options win):

| What | Option | Environment variable |
|---|---|---|
| Certificate | `--p12=PATH` | `USP_MP_P12` |
| Password, from a file (first line) | `--p12-password-file=PATH` | `USP_MP_P12_PASS_FILE` |
| Password, as a value (empty means none) | `--p12-password=VALUE` | `USP_MP_P12_PASS` |
| Re-record the snapshot instead of comparing | `--update-demo-snapshot` | |

```bash
# only the demo tests
pytest -m demo --p12=$HOME/secrets/usp.p12 --p12-password-file=$HOME/secrets/usp.pw

# everything, configured through the environment
export USP_MP_P12=$HOME/secrets/usp.p12 USP_MP_P12_PASS_FILE=$HOME/secrets/usp.pw
pytest
```

Notes:

- Write the options with `=`, as above. With `--p12 PATH`, pytest mistakes the path for a test location before it reads the project configuration, and then reports the option as unknown.
- Prefer a password file (mode `0600`) over `--p12-password` or `USP_MP_P12_PASS`, since a value on the command line is visible to other users in the process list.
- Keep the certificate and password file outside the repository. `*.p12` is in `.gitignore` as a safety net, but a password file is not.
- If a certificate is given without any password source, or the file doesn't exist, pytest stops with a usage error instead of silently skipping. A wrong password makes the demo tests fail, not skip.
- The tests only ever use the demo endpoint and refuse to run against production. They call close and delete, which per the vendor HowTo succeed on the demo without changing its mock data.
- `pytest --help` lists the options under "Mein Postkorb demo-service tests".

# License

BSD 3-Clause; see [LICENSE](LICENSE).

# References

* Austrian [Unternehmensserviceportal](https://www.usp.gv.at/)
* [Mein Postkorb Automatische Abholung HowTo](https://www.usp.gv.at/at.gv.mpk-p/aadocs/Mein_Postkorb_AutomatischeAbholung_HowTo.pdf)
