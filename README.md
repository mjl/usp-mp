


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

# References

* Austrian [Unternehmensserviceportal](https://www.usp.gv.at/)
* [Mein Postkorb Automatische Abholung HowTo](https://www.usp.gv.at/at.gv.mpk-p/aadocs/Mein_Postkorb_AutomatischeAbholung_HowTo.pdf)
