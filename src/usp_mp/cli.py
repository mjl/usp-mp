import argparse
import getpass
import logging.config
import os
import sys
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='uspmp',
        description='Query and download deliveries from the Austrian Unternehmensserviceportal — Mein Postkorb service.',
    )
    parser.add_argument('--client-certificate', required=True, help='Path to the .p12 certificate bundle file')
    password = parser.add_mutually_exclusive_group()
    password.add_argument('--client-password', help=(
        'Password for the .p12 file: ENV:var_name reads it from the environment, an empty string means no password. '
        'A literal password is visible to other users (ps); prefer ENV:, --client-password-file or the prompt '
        '(used when no password option is given)'
    ))
    password.add_argument('--client-password-file', metavar='PATH', help='Read the .p12 password from the first line of this file')
    parser.add_argument('--save', action='store_true', help='Save documents locally', default=False)
    parser.add_argument('--download', help='Directory to store downloads', default='.')
    parser.add_argument('--all', action='store_true', help='Fetch all deliveries (or just new)', default=False)
    parser.add_argument('--prod', action='store_true', help='Use production endpoint (defaults to demo service)', default=False)
    parser.add_argument('--debug', action='store_true', help='Trace service calls and show full tracebacks', default=False)
    return parser


def read_password(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """Resolve the .p12 password from the options, environment, a file, or a prompt."""

    if args.client_password_file is not None:
        try:
            with open(args.client_password_file, encoding='utf-8') as f:
                return f.readline().rstrip('\r\n')
        except OSError as e:
            parser.error(f"cannot read password file: {e}")

    p12_pass = args.client_password
    if p12_pass is None:
        return getpass.getpass(f"Enter {args.client_certificate} password: ")

    if p12_pass.startswith("ENV:"):
        env_name = p12_pass[4:]
        if env_name not in os.environ:
            parser.error(f"environment variable {env_name} (from --client-password) is not set")
        return os.environ[env_name]

    if p12_pass:
        print("uspmp: warning: a password on the command line is visible to other users; "
              "use ENV:var_name, --client-password-file or the prompt instead", file=sys.stderr)
    return p12_pass


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not Path(args.client_certificate).is_file():
        parser.error(f"certificate file {args.client_certificate} does not exist")
    dest = Path(args.download)
    if not dest.is_dir():
        parser.error(f"download directory {args.download} is not a directory")

    p12_pass = read_password(parser, args)

    if args.debug:
        setup_logging()

    try:
        process(args, dest, p12_pass)
    except KeyboardInterrupt:
        print("uspmp: interrupted", file=sys.stderr)
        return 130
    except Exception as e:
        if args.debug:
            raise
        print(f"uspmp: error: {e}", file=sys.stderr)
        return 1
    return 0


def process(args: argparse.Namespace, dest: Path, p12_pass: str) -> None:
    with Mailbox(args.client_certificate, p12_pass, prod=args.prod) as mbox:
        for post_idx, delivery in enumerate(mbox.deliveries(all=args.all)):
            process_delivery(args, dest, post_idx, delivery)


def process_delivery(args: argparse.Namespace, dest: Path, post_idx: int, delivery) -> None:
    print(f"[{post_idx:02}] {delivery.delivery_ts} {delivery}")

    save_path = dest / str(post_idx)
    if args.save:
        save_path.mkdir(exist_ok=False)

    for att_idx, attachment in enumerate(delivery.attachments()):
        print(f"   [{att_idx:02}] {attachment} {attachment.mimetype}")
        b = attachment.content(verify_checksum=True)
        if att_idx == 0 and attachment.mimetype == 'text/plain' and len(b) < 1000:
            print("     -----")
            for line in b.split(b'\n'):
                print(" "*4, line.decode(errors='replace'))
            print("     -----")
        if args.save:
            fname = save_path / f"{att_idx}_{attachment.sanitized_filename}"
            with open(fname, 'wb') as f:
                f.write(b)
            print("        Saved as", fname)

    if args.save:  # Only mark as read if saved
        delivery.close()

    print("\n=================================")


if __name__ == "__main__":
    raise SystemExit(main())
