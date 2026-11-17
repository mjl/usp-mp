# USP Mein Postkorb Autoabholung

This library wraps the Austrian Government's "Unternehmensserviceportal — Mein Postkorb" SOAP/REST API in a convenient Python abstraction.

## Prerequisites

- USP user with Postbevollmächtigter role to activate "Autoabholung".
- USP admin to generate client certificates.
- Client certificate bundle (.p12) and its password.

## Certificate setup checklist

- Activate _Autoabholung_ in _Mein Postkorb_ (by someone with role `Postbevollmächtigter`).
- Generate and download a client certificate (.p12) in the Autoabholung section:
  "Settings" -> "Automatische Abholung" -> "Zertifikat erzeugen" (client)
- Keep the .p12 password; it is required by the client.

## Quick start (CLI)

A small demo utility is included.

```bash
uspmp \
  --client-certificate /path/to/certificate.p12 \
  --client-password ENV:USP_P12_PASS
```

Options:

- `--client-certificate` path to the `.p12` file (required)
- `--all` fetch all (not just new) deliveries
- `--save` save attachments under `--download` (default: current directory) and mark each saved delivery as read
- `--prod` use production endpoint (default: demo)
- `--debug` trace SOAP calls and show full tracebacks on errors

Password handling:

- `--client-password ENV:VARNAME` reads the password from the environment variable (an unset variable is an error).
- `--client-password-file PATH` reads it from the first line of a file.
- Without either option, you are prompted.
- `--client-password ''` means the `.p12` has no password. A literal password also works, but it is visible to other users in the process list, so `uspmp` prints a warning.

Exit status: 0 on success, 1 on errors (such as a wrong password or a service error, reported as a single line), 2 on invalid arguments.

## Quick start (library)

```python
from usp_mp.mail import Mailbox

p12_path = "/path/to/certificate.p12"
p12_pass = "secret"

with Mailbox(p12_path, p12_pass, prod=False) as mbox:
    for delivery in mbox.deliveries(all=False):
        print(delivery.delivery_ts, delivery.subject)
        for attachment in delivery.attachments():
            data = attachment.content(verify_checksum=True)
            # process data
```

## Library API

### Mailbox

`Mailbox(p12_path, p12_pass, prod=False)` loads the client certificate for the demo or production endpoint. It must be used as a context manager: `with Mailbox(...) as mbox:` opens one connection (HTTP session and SOAP client) that all calls inside the block share, and closes it when the block ends, even on an exception.

- Calling `deliveries()` outside a `with` block, or after it ended, raises `MailboxStateError`.
- After the block, `Delivery` and `Attachment` objects keep their already-fetched data (subject, file names, …), but anything that contacts the service (`content()`, `close()`, `delete()`) raises `MailboxStateError`.
- A `Mailbox` can be opened only once; create a new one for another session. `mbox.close()` closes it early and is safe to call more than once.

`Mailbox.deliveries()` yields `Delivery` objects.

- `all=True` returns all deliveries in the mailbox, paging through it 1000 at a time.
- `all=False` returns unread deliveries only. The service can't page these, so at most 1000 are returned per call, and a warning is logged (logger `usp_mp.mail`) when there are more. Close the processed deliveries and call again for the rest. On the demo service, the limit is applied to the whole mailbox before unread deliveries are picked, so fewer may come back; the same warning covers that.
- `limit=N` caps the number of deliveries returned.
- The delivery IDs are queried when `deliveries()` is called; each delivery's details are fetched as you iterate. So closing or deleting deliveries inside the loop is safe and doesn't skip any.

### Delivery

A delivery is akin to an email in Mein Postkorb lingo. As such, it contains metadata (like subject, delivery timestamp, sender, recipient etc.) and attachments.
`Delivery.attachments()` yields `Attachment` objects.

Methods:

- `close()` marks the delivery as read.
- `delete()` deletes a closed (ie. read) delivery. It should refuse deleting unread deliveries.

Example:

```python
with Mailbox(p12_path, p12_pass) as mbox:
    for delivery in mbox.deliveries(all=False):
        delivery.close()
        delivery.delete()
```

### Attachment

An Attachment contains a part of a delivery. There is always at least one part called `mailbody` of type `text/plain`.
A delivery can contain any number of Attachments.

`Attachment.content(verify_checksum=True)` downloads the attachment via REST and optionally validates the checksum.

**Example: save attachments per delivery using sanitized file names**

```python
from pathlib import Path
from usp_mp.util import unique_path

out_dir = Path("/var/tmp/uspmp")
out_dir.mkdir(parents=True, exist_ok=True)

with Mailbox(p12_path, p12_pass) as mbox:
    for delivery in mbox.deliveries(all=False):
        delivery_dir = out_dir / delivery.id
        delivery_dir.mkdir(exist_ok=True)
        for attachment in delivery.attachments():
            data = attachment.content(verify_checksum=True)
            unique_path(delivery_dir, attachment.sanitized_filename).write_bytes(data)
```

`sanitized_filename` lower-cases the name, folds accents to ASCII (`März` → `marz`), replaces every run of other characters with a single `_`, and picks the extension from the MIME type unless the original one already matches. Two attachments can still end up with the same name; `unique_path` appends `_1`, `_2`, … instead of overwriting. Both helpers, `sanitize_filename(name, mime)` and `unique_path(directory, filename)`, are in `usp_mp.util`.

### Errors

All exceptions derive from `usp_mp.UspMpError`, so one `except UspMpError` catches everything the library raises on purpose:

| Exception | Raised when | Also a |
|---|---|---|
| `CertificateError` | The `.p12` can't be read, the password is wrong, or it contains no key and certificate | |
| `ApiError` | The service answers with an `Error`; has `.code`, `.message` and `.operation` (for example `GetDelivery`) | `ValueError` |
| `ChecksumError` | A downloaded attachment doesn't match its checksum, or the algorithm is unsupported | `ValueError` |
| `MailboxStateError` | The mailbox isn't open yet, or is already closed | `RuntimeError` |

```python
from usp_mp import ApiError, CertificateError, Mailbox

try:
    with Mailbox(p12_path, p12_pass) as mbox:
        ...
except CertificateError as e:
    print("check the certificate and password:", e)
except ApiError as e:
    print("service error", e.code, e.message, "in", e.operation)
```

Network problems surface as the underlying `requests` exceptions (for example `requests.ConnectionError`), and HTTP errors on attachment downloads as `requests.HTTPError`.

## Internals

### Working with raw SOAP methods

An open mailbox exposes the zeep service proxy if you need to call SOAP operations directly.

```python
with Mailbox(p12_path, p12_pass, prod=True) as mbox:
    # Call any SOAP operation directly
    mbox.service.GetDelivery(delivery_id, Version=mbox.uspmp.VERSION)
```

Underneath, `Mailbox` uses `USP_MP_Adapter.connection()`, a context manager for one HTTP session and zeep client; it can also be used on its own.

### Attachment-only download (REST)

Each attachment is downloaded via GET and identified by `delivery_id` and `attachment_id`.

```text
https://autoabholung.meinpostkorb.brz.gv.at/attachment?delivery_id=<uuid>&attachment_id=<uuid>
```

The response `Content-Type` is the attachment MIME type.

### Notes

- The BRZ CA certificate and WSDL are shipped under `src/usp_mp/certs/` and `src/usp_mp/wsdl/`.
- Demo mode returns fixed sample data and ignores close/delete effects.
- TLS uses your .p12 client cert and the BRZ CA certificate; no extra trust store setup is required when using the library.
