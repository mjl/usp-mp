import argparse
import getpass
import logging.config
import os
from pathlib import Path

from usp_mp.mail import Mailbox

def setup_logging():
    logging.config.dictConfig({
        'version': 1,
        'formatters': {
            'verbose': {
                'format': '%(name)s: %(message)s'
            }
        },
        'handlers': {
            'console': {
                'level': 'DEBUG',
                'class': 'logging.StreamHandler',
                'formatter': 'verbose',
            },
        },
        'loggers': {
            'zeep.transports': {
                'level': 'DEBUG',
                'propagate': True,
                'handlers': ['console'],
            },
        }
    })

def main():
    parser = argparse.ArgumentParser(description='Query and download deliveries from the Austrian Unternehmens-Service-Portal — Mein Postfach service.')
    parser.add_argument('--client-certificate', help='Path to the .p12 certificate bundle file')
    parser.add_argument('--client-password', help='Password for the .p12 file (prompts if not provided, pass empty string for no password, use ENV:var_name for reading from environment)')
    parser.add_argument('--save', action='store_true', help='Save documents locally', default=False)
    parser.add_argument('--download', help='Directory to store downloads', default='.')
    parser.add_argument('--all', action='store_true', help='Fetch all deliveries (or just new)', default=False)
    parser.add_argument('--prod', action='store_true', help='Use production endpoint (defaults to demo service)', default=False)
    parser.add_argument('--debug', action='store_true', help='Trace service calls', default=False)

    args = parser.parse_args()

    dest = Path(args.download)
    if not dest.is_dir():
        raise ValueError(f"{args.download} ist not a directory")

    # Prompt for password if not provided
    p12_pass = args.client_password
    if p12_pass and p12_pass.startswith("ENV:"):
        env_name = p12_pass[4:]
        p12_pass = os.environ.get(env_name)
    if p12_pass is None:
        p12_pass = getpass.getpass(f"Enter {args.client_certificate} password: ")

    if args.debug:
        setup_logging()

    save_path = Path()

    mbox = Mailbox(args.client_certificate, p12_pass, prod=args.prod)
    for post_idx, delivery in enumerate(mbox.deliveries(all=args.all)):
        print(f"[{post_idx:02}] {delivery.delivery_ts} {delivery}")

        if args.save:
            save_path = dest / str(post_idx)
            save_path.mkdir(exist_ok=False)

        for att_idx, attachment in enumerate(delivery.attachments()):
            print(f"   [{att_idx:02}] {attachment} {attachment.mimetype}")
            b = attachment.content(verify_checksum=True)
            if att_idx == 0 and attachment.mimetype == 'text/plain' and len(b) < 1000:
                print("     -----")
                for line in b.split(b'\n'):
                    print(" "*4, line.decode())
                print("     -----")
            if args.save:
                fname = save_path / f"{att_idx}_{attachment.sanitized_filename}"
                with open(fname, 'wb') as f:
                    f.write(b)
                print("        Saved as", fname)

        if args.save:  # Only mark as read if saved
            delivery.close()

        print("\n=================================")

    return 0

if __name__ == "__main__":
    SystemExit(main())
