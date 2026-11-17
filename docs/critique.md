# Project critique: usp-mp

Review of a small Python client for the *Mein Postkorb* SOAP/REST service. First written at commit `9085f5f`, updated against `3c10a6f`. The core design is sound (`adapter` → `mail`, `Mailbox → Delivery → Attachment`), but it is early-stage code with real bugs, a key-handling weakness, and no tests.

Status markers: **[fixed]** resolved since the first review, **[open]** still applies. Nothing here was run against the demo service (needs a client certificate); findings come from reading the code, the bundled WSDL/XSD schemas and certificate, and the vendor HowTo. Items marked *verify* should be confirmed empirically.

The plan for fixing every open item is in [fix-plan.md](fix-plan.md); its "Facts checked while planning" section is the evidence behind the schema-based notes below.

**Progress:** 7 of the original findings are fixed (exit status, `requires-python`, console script, `.gitignore`, typos/README install, private-key handling, attachment download through the session) plus one new bug found and fixed along the way. A test harness is in place (see Testing). Everything else is still open.

## Bugs

1. **[fixed] CLI never returned an exit status.** `SystemExit(main())` built the exception without raising it. Now `raise SystemExit(main())` (`src/usp_mp/cli.py:92`), and the `uspmp` console script uses `sys.exit(main())`.
2. **[open] Results silently truncated at 100.** `mail.py:42,45` hardcode `Limit=100` (and `Start=0`) in both selectors. `all=True` reads as "everything", but only the first 100 are returned. Per the schema, `Limit` may be 1–1000 and `HitCount` is the total. `AllDeliveriesType` can be paged with `Paging(Start, Limit)`, but `NewDeliveriesOnlyType` takes only `Limit` and **cannot be paged**, so new-only queries can at best raise the limit to 1000 and warn when `HitCount` is larger. When paging, collect all IDs before yielding: closing or deleting deliveries mid-iteration would otherwise shift server-side offsets and skip messages.
3. **[open] Checksum handling has dead code; the logic itself looks correct.** `hashlib.new()` raises `ValueError` for an unknown algorithm; it never returns `None`, so the `if hash is None` branch (`mail.py:87`) is dead code and its `KeyError` unreachable. The earlier *verify* concerns are mostly answered by the schema: `AlgorithmID` is an enumeration of `SHA256` or `SHA512`, both accepted by `hashlib.new()` (tested on 3.11 and 3.14), and `Value` is `base64Binary`, which zeep decodes to `bytes`, so comparing it to `hash.digest()` is right. Still *verify* once against the demo service.
4. **[open] `raise_on_error` is loosely typed and brittle.** Annotated `None | Never`; assumes every response has `.Error` and relies on zeep's `in` semantics. The schema makes responses a `choice` of result or `Error`, and an `Error` always has both `ErrorCode` and `ErrorMessage`, so a simple `getattr(r, "Error", None) is not None` check suffices. With `strict=False` a malformed response can still pass silently.
5. **[open] `Mailbox.deliveries()` is a generator holding a live connection.** Since the key-handling fix it no longer keeps key files around, only an HTTP session; a caller that `break`s early leaves closing that session to GC. Each `Delivery(...)` also does a blocking `GetDelivery`, so N deliveries = N+1 round trips, lazily.
6. **[open] Builtin shadowing.** `all` (parameter, `mail.py:33`) and `hash` (local, `mail.py:86`) shadow builtins.
7. **[open] CLI argument handling.** `--client-certificate` is not `required=True`, so omitting it crashes later in `open(None)`. An `ENV:` variable that is unset yields `None` and silently falls back to prompting. A bad `--download` path raises a bare `ValueError` traceback instead of an argparse error.
8. **[open] `--save` directory naming is unstable.** `dest / str(post_idx)` with `exist_ok=False`: a second run crashes, and index `0` refers to different deliveries on different days. Use the delivery ID (as `docs/howto.md` already does).
9. **[open] Filename sanitising quirks.** `.replace("__", "_")` collapses only one pass (`a___b` → `a__b`); use `re.sub("[^a-z0-9_-]+", "_", …)`. The extension comes from the server-supplied MIME type, so a mislabeled file gets the wrong extension. Two attachments can sanitise to the same name and overwrite each other in the library example.
10. **[fixed] `usp_mp.mail` could not be imported on Python < 3.14.** New finding. `Mailbox.deliveries()` is annotated `-> Iterator[Delivery]` before `Delivery` is defined; only 3.14's deferred annotation evaluation made that work, so the `>=3.14` pin was hiding a `NameError` on 3.11–3.13. Fixed with `from __future__ import annotations` in `mail.py`; import verified on 3.11–3.14.
11. **[open, *verify*] Query requests differ from the documented form.** New finding while writing the request-shape tests. The vendor HowTo sends `<aa:AllDeliveries>` / `<aa:NewDeliveriesOnly>`, which are substitution-group members of `aa:Query`. zeep cannot emit substitution elements (the same limitation behind the `strict=False` workaround below) and sends `<aa:Query xsi:type="aa:AllDeliveriesType">` instead, even when given the `AllDeliveries` element. That is schema-valid, because `Query` itself is not abstract (only its type is), and the client has presumably always worked this way. But it is not what the vendor documents, so a stricter server-side validator could reject it. `tests/test_demo.py::test_query_accepted` confirms acceptance once run with a certificate; if it ever fails, the fix is to build the `QueryDeliveriesRequest` body by hand (e.g. zeep's `xsd.AnyObject` or a raw lxml element).

## Security

- **[fixed] Private key written to disk in plaintext.** `ConnectionContext.__enter__` used to write the decrypted key to `NamedTemporaryFile(delete=False)` for the whole connection, leaking it on a crash or a failure inside `__enter__`. Now `USP_MP_Adapter.build_ssl_context` builds one `SSLContext` per adapter: the key is written only **encrypted** with a random one-time password, into a 0700 temp dir that is removed right after `load_cert_chain` (also on error), and the context is mounted on the session via `SSLContextAdapter`. The plaintext `key_pem` attribute is gone. Python's `ssl` cannot load keys from memory, so this is the closest equivalent. Covered by `tests/test_adapter_tls.py` (mutual TLS against a local server, 3.11–3.14).
- **[fixed] `download_attachment` bypassed the session.** It now uses `self.session.get(..., timeout=TIMEOUT)`, so it gets the client certificate from the mounted context, the User-Agent, connection reuse, and a timeout.
- **[open] Password on the command line** (`--client-password`) is visible in `ps`. `ENV:` and the prompt are safer; the plain form should be discouraged or removed.
- **[open] `strict=False` for all calls** to work around one known parse error (`CorporateBody` vs `Person`) disables validation everywhere. Cause, from the schema: `ContactType` (`zuseaa_p2.xsd:145`) and `zuse_p2.xsd:674` reference the abstract element `p:Person`, which `p:CorporateBody` replaces via an XSD substitution group, and zeep's strict parser rejects the substitution. A documented local patch (an explicit `choice` of `PhysicalPerson`/`CorporateBody`) may allow `strict=True`; *verify* against the demo service.

## Design

All **[open]**.

- **Zeep client built twice, per connection.** `read_wsdl_types` parses the WSDL once in `__init__`, and every `ConnectionContext` parses it again. Zeep WSDL parsing is slow; build once and reuse.
- **`self.service = None` then `assert connection.service` everywhere.** Initialise fully in the constructor so the type isn't `Optional`.
- **`types = ParamTypes()` is a class attribute mutated per instance via `setattr`.** Shared global state behind an instance API; not thread-safe.
- **Zeep objects leak out** via `Delivery.delivery` and `Attachment.attachment`. Sender, recipient and other metadata mentioned in the docs have no accessors.
- **Error handling.** `Mailbox.__init__` catches `Exception` and re-raises `RuntimeError` without `from e`, so a wrong password and a corrupt file are indistinguishable. Everything else is bare `ValueError`. A small exception hierarchy carrying the API error code would let callers react.
- **Sanitising belongs in a utility**, not on `Attachment`.

## Packaging and repo hygiene

- **[fixed] `requires-python`** lowered from `>=3.14` to `>=3.11` (see bug 10 for the code change this needed).
- **[fixed] Console script.** `uspmp.py` moved to `src/usp_mp/cli.py` and exposed as `[project.scripts] uspmp = "usp_mp.cli:main"`; `docs/howto.md` updated. `python uspmp.py` no longer exists.
- **[fixed] README "Installation"** section is filled in. It uses a `<repository-url>` placeholder on purpose, because the origin remote is private.
- **[fixed] `.gitignore`** rewritten: stray `:` and merged pattern removed; the blanket `.*` rule (which would have hidden e.g. `.github/`) and unused template sections dropped; `.jj/` added; `*.cer` no longer blocks the bundled CA cert (`!src/usp_mp/certs/*.cer`).
- **[fixed] Typos and naming** ("ist not a directory", "rom private_key", "instanciate", "Mein Postfach", "Unternehmer-Service-Portal", "Postbevollmaechtigter", "return" → "returns", "meta data").
- **[open]** `description = "Add your description here"` in `pyproject.toml` is still the template placeholder.
- **[open]** No licence file.
- **[open]** `brz_ca.cer` is pinned and shipped with no note on source, fingerprint or expiry. It is `CN=BRZ-StammCA-2-02, O=Bundesrechenzentrum GmbH`, valid until **2038-01-12**, so rotation is not urgent; still document its details, origin, and how to refresh it.
- **[open]** Version `1.0.20260224` mixes semver and CalVer and is hand-edited.
- **[open]** The library has no `logging`; debug logging is configured only inside the CLI module.
- **[open]** `docs/` contains the vendor PDF, a `.txt` copy of it, and a zip. Useful reference, but redistribution rights are unclear; consider linking instead.

## Testing

**[fixed]** The Phase 0 harness exists; see README "Development" for how to run it. It passes on 3.11–3.14 with no certificate:

- `tests/fakes.py` + `conftest.py`: an in-memory fake of the service. Responses are real zeep objects built from the bundled WSDL, so fakes cannot use fields the schema lacks. Error codes and HTTP 400 follow the vendor HowTo.
- `test_mail.py`, `test_cli.py`: current behaviour of `Mailbox`/`Delivery`/`Attachment` and the CLI (querying, metadata, close/delete, checksum verification, API errors, filename sanitising, `--save`, `ENV:` passwords).
- `test_wsdl.py`: the bundled WSDL loads and produces the expected request XML (see B11). `test_import.py`: import smoke test (guards B10). `test_adapter_tls.py`: client-certificate handling.
- Known open bugs are `xfail(strict=True)` tests: B2 (paging), B7 (missing certificate), B8 (`--save` rerun), B9 (underscores). Fixing one makes its test fail until the marker is removed.
- `test_demo.py`: opt-in, read-only demo-service tests (`USP_MP_P12`, `USP_MP_P12_PASS`). Not yet run, so the *verify* items above stay open until someone with a certificate runs it.

Still missing tests for the fixes themselves (pagination, exception hierarchy, etc.); those come with each phase.

## Strengths

- Small, clear surface that maps to the domain.
- WSDL and CA bundled, so no dependency on the portal's `?wsdl`; comments explain why.
- Checksum verification on by default; the CLI only marks deliveries read after saving them.
- Docs exist and are mostly accurate, including the demo-vs-prod caveat; installation is now documented.
- Type hints and `py.typed` shipped; the CLI is a proper installed command.

## Suggested order of work (remaining)

See [fix-plan.md](fix-plan.md) for the detailed, phased plan, PR sequence, and the decisions it needs from the maintainer. In short:

1. ~~Test scaffolding~~ (done); run `pytest -m demo` once with a certificate to settle the *verify* items.
2. Quick fixes: checksum dead code, filename sanitising.
3. CLI hardening: required `--client-certificate`, clean argument errors, stable `--save` directories.
4. Exception hierarchy, `raise_on_error`, and pagination.
5. Connection refactor: build the zeep client once (the private-key part is done).
6. Logging, typed accessors, and the `strict=False` investigation.
7. Licence, project description, CA documentation, versioning, vendor documents.
