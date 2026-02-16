import argparse
import getpass
import logging.config
import os

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
    parser.add_argument('--all', action='store_true', help='Fetch all deliveries (or just new)', default=False)
    parser.add_argument('--prod', action='store_true', help='Use production endpoint (defaults to demo service)', default=False)
    parser.add_argument('--debug', action='store_true', help='Trace service calls', default=False)

    args = parser.parse_args()

    # Prompt for password if not provided
    p12_pass = args.client_password
    if p12_pass and p12_pass.startswith("ENV:"):
        env_name = p12_pass[4:]
        p12_pass = os.environ.get(env_name)
    if p12_pass is None:
        p12_pass = getpass.getpass("Enter .p12 password: ")

    if args.debug:
        setup_logging()

    mbox = Mailbox(args.client_certificate, p12_pass, prod=args.prod)
    for delivery in mbox.deliveries(all=args.all):
        print(delivery.delivery_ts, delivery)

        for attachment in delivery.attachments():
            print("  >", attachment)
            b = attachment.content(verify_checksum=True)

    return 0

if __name__ == "__main__":
    SystemExit(main())
