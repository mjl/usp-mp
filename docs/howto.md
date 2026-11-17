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

- `--all` fetch all (not just new) deliveries
- `--prod` use production endpoint (default: demo)
- `--debug` trace SOAP calls

Password handling:

- If `--client-password` is omitted, you are prompted; `ENV:VARNAME` reads the password from the environment.

## Quick start (library)

```python
from usp_mp.mail import Mailbox

p12_path = "/path/to/certificate.p12"
p12_pass = "secret"

mbox = Mailbox(p12_path, p12_pass, prod=False)

for delivery in mbox.deliveries(all=False):
    print(delivery.delivery_ts, delivery.subject)
    for attachment in delivery.attachments():
        data = attachment.content(verify_checksum=True)
        # process data
```

## Library API

### Mailbox

`Mailbox(p12_path, p12_pass, prod=False)` opens a client bound to the demo or production endpoint.

`Mailbox.deliveries()` yields `Delivery` objects.

- `all=True` returns all deliveries in the mailbox.
- `all=False` returns unread deliveries only.
- Results are capped at 100 by the built-in selector; use pagination via SOAP if you need more (not yet implemented).

### Delivery

A delivery is akin to an email in Mein Postkorb lingo. As such, it contains metadata (like subject, delivery timestamp, sender, recipient etc.) and attachments.
`Delivery.attachments()` yields `Attachment` objects.

Methods:

- `close()` marks the delivery as read.
- `delete()` deletes a closed (ie. read) delivery. It should refuse deleting unread deliveries.

Example:

```python
for delivery in mbox.deliveries(all=False):
    delivery.close()
    delivery.delete()
```

### Attachment

An Attachment contains a part of a delivery. There is always at least one part called `mailbody` of type `text/plain`.
A delivery can contain any number of Attachments.

`Attachment.content(verify_checksum=True)` downloads the attachment via REST and optionally validates the checksum.

**Example: save attachments per-delivery using a sanitized filenames**

```python
from pathlib import Path

out_dir = Path("/var/tmp/uspmp")
out_dir.mkdir(parents=True, exist_ok=True)

for delivery in mbox.deliveries(all=False):
    delivery_dir = out_dir / delivery.id
    delivery_dir.mkdir(exist_ok=True)
    for attachment in delivery.attachments():
        data = attachment.content(verify_checksum=True)
        (delivery_dir / attachment.sanitized_filename).write_bytes(data)
```

## Internals

### Working with raw SOAP methods

The adapter exposes the raw zeep client and SOAP service if you need to call other operations.

```python
from usp_mp.adapter import USP_MP_Adapter

uspmp = USP_MP_Adapter(p12_path, p12_pass, prod=True)
with uspmp.connection() as conn:
    # Call any SOAP operation directly
    conn.service.GetDelivery(delivery_id, Version=uspmp.VERSION)
```

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
