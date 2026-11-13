"""See and resolve outside actions whose outcome is unknown.

When ToroForge or OpenDental does not answer, the app parks the action and refuses
to repeat it until someone checks what really happened. Check the provider first,
then record the answer here. Every resolution is written to the audit log.

    .venv/bin/python -m tools.stuck_operations list
    .venv/bin/python -m tools.stuck_operations auto --dry-run      # what the automatic check would do
    .venv/bin/python -m tools.stuck_operations auto                # resolve every clear case (Render Cron Job)
    .venv/bin/python -m tools.stuck_operations resolve <id> --happened --set TX_ID=abc123 --by "Funmi" --note "seen in ToroForge"
    .venv/bin/python -m tools.stuck_operations resolve <id> --not-done --by "Funmi" --note "no transaction in ToroForge"

`auto` only reads from ToroForge and OpenDental. It resolves an action when exactly one
provider record matches it, or when nothing shows after an hour (10 minutes for OpenDental);
everything else is left for `resolve`. The CRM worker settles timed-out OpenDental bookings
and patients itself with the same rules, as soon as OpenDental answers again.

Values needed with --happened (read them off the provider):
    wallet_transfer: TX_ID        appointment_create: AptNum     patient_create: PatNum
    wallet_keystore: address      wallet_tns: address, username  funding_start: TX_ID
    wallet_password_rotation, commslog, popup: nothing extra
"""
import argparse
import asyncio
import sys
from uuid import UUID

from app.infrastructure.database import AsyncSessionLocal, engine
from app.infrastructure.providers.opendental import openDentalApi
from app.infrastructure.providers.toroforge.client import ToroForgeClient
from app.infrastructure.providers.toroforge.config import get_toroforge_config
from app.infrastructure.providers.toroforge.keystore_client import ToroForgeKeyStoreClient
from app.infrastructure.providers.toroforge.payment_client import ToroForgePaymentClient
from app.infrastructure.providers.toroforge.tns_client import ToroForgeTNSClient
from app.infrastructure.providers.toroforge.wallet_transfer_client import ToroForgeWalletTransferClient
from app.services import reconciliation_service as reconciliation
from app.services.errors import ServiceError
from app.services.reconciliation_checks import ProviderChecks


def format_age(age) -> str:
    minutes = int(age.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    return f"{hours} h" if hours < 48 else f"{hours // 24} days"


async def show_list() -> int:
    async with AsyncSessionLocal() as db:
        items = await reconciliation.list_unknown_outcomes(db)
    if not items:
        print("Nothing is stuck. Every outside action has a known outcome.")
        return 0
    print(f"{len(items)} action(s) with an unknown outcome:\n")
    for item in items:
        flag = "  (started recently; may still be running)" if item.may_still_be_running else ""
        print(f"  {item.id}  {item.action}  {item.state}, {format_age(item.age)} old{flag}")
        for name, value in item.context.items():
            print(f"      {name}: {value}")
        print(f"      check: {item.where_to_check}\n")
    print("Resolve each with: python -m tools.stuck_operations resolve <id> --happened|--not-done --by <name>")
    return 0


async def run_auto(args) -> int:
    client = ToroForgeClient(config=get_toroforge_config(), breaker=ToroForgeClient.toroforge_breaker)
    try:
        async with AsyncSessionLocal() as db:
            checks = ProviderChecks(
                db, transfers=ToroForgeWalletTransferClient(client), payments=ToroForgePaymentClient(client),
                tns=ToroForgeTNSClient(client), keystore=ToroForgeKeyStoreClient(client), opendental_for=openDentalApi)
            results = await reconciliation.auto_resolve(db, checks, dry_run=args.dry_run)
    finally:
        await client.aclose()

    if not results:
        print("Nothing is stuck. Every outside action has a known outcome.")
        return 0
    if args.dry_run:
        print("Dry run: nothing was changed.\n")
    for result in results:
        print(f"  {result.item.id}  {result.item.action}  {format_age(result.item.age)} old: {result.outcome.upper()}")
        print(f"      {result.summary}")
    counts = {}
    for result in results:
        counts[result.outcome] = counts.get(result.outcome, 0) + 1
    print("\n" + ", ".join(f"{count} {outcome}" for outcome, count in counts.items()))
    if counts.get("needs a person"):
        print("Check those at the provider, then: python -m tools.stuck_operations resolve <id> --happened|--not-done --by <name>")
    return 0


async def run_resolve(args) -> int:
    fields = {}
    for pair in args.set or []:
        name, separator, value = pair.partition("=")
        if not separator:
            print(f"--set expects NAME=VALUE, got '{pair}'")
            return 2
        fields[name.strip()] = value.strip()
    async with AsyncSessionLocal() as db:
        try:
            summary = await reconciliation.resolve(
                db, args.id, happened=args.happened, resolved_by=args.by, note=args.note or "",
                fields=fields, force=args.force)
        except ServiceError as exc:
            await db.rollback()
            print(f"Not resolved: {exc.detail}")
            return 1
    print(summary)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="See and resolve outside actions whose outcome is unknown.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="show every stuck action")
    auto = commands.add_parser("auto", help="check stuck actions with ToroForge and OpenDental and resolve the clear ones")
    auto.add_argument("--dry-run", action="store_true", help="report what would be resolved without changing anything")
    resolve = commands.add_parser("resolve", help="record what the provider shows for one action")
    resolve.add_argument("id", type=UUID)
    outcome = resolve.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--happened", action="store_true", help="the provider shows the action took place")
    outcome.add_argument("--not-done", dest="happened", action="store_false", help="the provider shows it did not")
    resolve.add_argument("--set", action="append", metavar="NAME=VALUE", help="value read off the provider, e.g. TX_ID=abc")
    resolve.add_argument("--by", required=True, help="who checked the provider")
    resolve.add_argument("--note", help="what you saw, for the audit log")
    resolve.add_argument("--force", action="store_true", help="resolve even if the call started under 10 minutes ago")
    resolve.add_argument("--yes", action="store_true", help="skip the confirmation question")
    args = parser.parse_args(argv)

    if args.command == "resolve" and not args.yes:
        outcome_text = "HAPPENED at the provider" if args.happened else "did NOT happen at the provider"
        answer = input(f"Record {args.id} as {outcome_text}, checked by {args.by}? Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            print("Cancelled; nothing changed.")
            return 1

    async def run():
        try:
            if args.command == "list":
                return await show_list()
            if args.command == "auto":
                return await run_auto(args)
            return await run_resolve(args)
        finally:
            await engine.dispose()

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
