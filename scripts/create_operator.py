#!/usr/bin/env python3
"""Create the first (or any) dashboard operator from the shell.

    python scripts/create_operator.py --email you@example.com --name "You" --role ADMIN

The password is read from stdin (prompted when interactive), never from an
argument, so it reaches neither the process list nor the shell history.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.db.session import async_session_maker
from app.models.operator import OPERATOR_ROLES
from app.services import auth


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", choices=OPERATOR_ROLES, default="ADMIN")
    args = parser.parse_args()
    password = getpass.getpass("Password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
    if len(password) < 12:
        print("password must be at least 12 characters", file=sys.stderr)
        return 2
    async with async_session_maker() as session:
        if await auth.get_operator_by_email(session, args.email):
            print("an operator with that email already exists", file=sys.stderr)
            return 1
        operator = await auth.create_operator(
            session, email=args.email, display_name=args.name, password=password, role=args.role
        )
    print(f"created operator {operator.email} ({operator.role})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
