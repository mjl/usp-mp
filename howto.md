# USP Mein Postkorb Autoabholung (Python) — How-To

This library wraps the Mein Postkorb “Automatische Abholung” SOAP/REST interface. You need a client certificate (.p12) and the server CA certificate from the USP settings page.

## Prerequisites

- USP user with Postbevollmaechtigter role to activate Autoabholung.
- USP admin to generate certificates.
- Client certificate bundle (.p12) with password.
- Server CA certificate (brz_ca.cer) for TLS validation.

## Certificate setup checklist

- Activate Autoabholung in Mein Postkorb (by Postbevollmaechtigter).
- Generate a client certificate (.p12) in the Autoabholung settings.
- Download the server CA certificate (brz_ca.cer).
- Keep the .p12 password; it is required by the client.
- USP UI labels: Settings -> Automatische Abholung -> "Zertifikat erzeugen" (client), "Server-Zertifikat" (CA).
- Role reminder: activation requires Postbevollmaechtigter; certificate management requires USP admin.

## Endpoints

- Demo: `https://demo-autoabholung.meinpostkorb.brz.gv.at/soap`
- Production: `https://autoabholung.meinpostkorb.brz.gv.at/soap`
- Attachment download: `https://autoabholung.meinpostkorb.brz.gv.at/attachment?delivery_id=...&attachment_id=...`

## Quick start (CLI)

```bash
python uspmp.py \
  --client-certificate /path/to/certificate.p12 \
  --client-password ENV:USP_P12_PASS
```

Options:

- `--all` fetch all (not just new) deliveries
- `--prod` use production endpoint (default: demo)
- `--debug` trace SOAP calls

## Quick start (library)

```python
from usp_mp.mail import Mailbox

p12_path = "/path/to/certificate.p12"
p12_pass = "secret"  # or read from env

mbox = Mailbox(p12_path, p12_pass, prod=False)  # demo by default

for delivery in mbox.deliveries(all=False):
    print(delivery.delivery_ts, delivery)
    for attachment in delivery.attachments():
        data = attachment.content(verify_checksum=True)
        # process data
```

## Attachment-only download (REST)

Each attachment is downloaded via GET and identified by `delivery_id` and `attachment_id`.

```text
https://autoabholung.meinpostkorb.brz.gv.at/attachment?delivery_id=<uuid>&attachment_id=<uuid>
```

## What the service provides

- Query deliveries (all or only new)
- Get a delivery by UUID
- Close (mark as read) a delivery
- Delete a closed delivery
- Download attachments via REST GET

## Notes

- Attachments are downloaded individually; use the `delivery_id` and `attachment_id` pair.
- Demo mode returns fixed sample data and ignores state changes.
- TLS must be configured with your client certificate and the server CA certificate.

## Troubleshooting

- 400 on attachment download usually means unknown `delivery_id`/`attachment_id`.
- Demo endpoint always returns the same data and ignores close/delete effects.
- Ensure the correct .p12 password and CA certificate are used for TLS.
