


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

Integration tests against the **demo** service are skipped unless a client certificate is configured. They only read; nothing is closed or deleted:

```bash
USP_MP_P12=/path/to/certificate.p12 USP_MP_P12_PASS=... pytest -m demo
```

# License

BSD 3-Clause; see [LICENSE](LICENSE).

# References

* Austrian [Unternehmensserviceportal](https://www.usp.gv.at/)
* [Mein Postkorb Automatische Abholung HowTo](https://www.usp.gv.at/at.gv.mpk-p/aadocs/Mein_Postkorb_AutomatischeAbholung_HowTo.pdf)
