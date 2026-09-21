"""Operator CLI. There is no admin web panel by design.

    python -m gateway.cli create-code --label "PC A" [--ttl 60] [--code CUSTOM]
    python -m gateway.cli installations
    python -m gateway.cli revoke --installation-id <id> [--reason ...]
    python -m gateway.cli audit [--limit 20]
    python -m gateway.cli reset-create-budget
    python -m gateway.cli health

The raw activation code is printed exactly once, in the operator's own terminal. It is
stored server-side as a digest only.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from .config import get_settings
from .models import AuditEvent, EnrollmentCode, GatewayCompute, Installation
from .security import activate_code, now, utc


def _db():
    from .database import SessionLocal

    return SessionLocal


def create_code(args) -> int:
    settings = get_settings()
    with _db()() as db:
        code, digest = activate_code(
            db, args.code or "", ttl_minutes=args.ttl or settings.activation_ttl_minutes, label=args.label
        )
    print("activation code (show it once, it is stored only as a digest):")
    print(code)
    print(f"label: {args.label or '-'}  ttl_minutes: {args.ttl or settings.activation_ttl_minutes}")
    print(f"code digest: {digest[:16]}…")
    return 0


def list_installations(args) -> int:
    with _db()() as db:
        rows = db.scalars(select(Installation).order_by(Installation.created_at)).all()
        print(f"{len(rows)} installation(s)")
        for row in rows:
            state = "revoked" if row.revoked_at else "active"
            print(
                f"{row.id}  {state:8s}  name={row.name or '-':<20.20s} "
                f"platform={row.platform or '-':<10.10s} version={row.client_version or '-':<10.10s} "
                f"created={utc(row.created_at).isoformat()} last_seen={utc(row.last_seen_at).isoformat()}"
            )
        control = db.get(GatewayCompute, 1)
        if control:
            print(
                f"compute: state={control.state} error={control.error_code or '-'} "
                f"session={control.active_session_id or '-'} create_attempts={control.create_attempts}"
            )
    return 0


def revoke(args) -> int:
    with _db()() as db:
        installation = db.get(Installation, args.installation_id)
        if installation is None:
            print("installation not found", file=sys.stderr)
            return 1
        installation.revoked_at = now()
        installation.revoked_reason = (args.reason or "operator_revoked")[:200]
        db.add(AuditEvent(operation="revoke", result="ok", installation_id=installation.id, created_at=now()))
        db.commit()
    print(f"revoked {args.installation_id}")
    return 0


def audit(args) -> int:
    with _db()() as db:
        rows = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(args.limit)).all()
        for row in rows:
            print(
                json.dumps(
                    {
                        "at": utc(row.created_at).isoformat(),
                        "installation_id": row.installation_id,
                        "operation": row.operation,
                        "result": row.result,
                        "error_code": row.error_code,
                        "detail": row.detail,
                    },
                    ensure_ascii=False,
                )
            )
    return 0


def reset_create_budget(args) -> int:
    with _db()() as db:
        control = db.get(GatewayCompute, 1)
        if control is None:
            print("no compute control row")
            return 1
        control.create_attempts = 0
        control.error_code = None
        db.commit()
    print("create attempt budget reset (confirm no Pod exists for the Volume before retrying)")
    return 0


def health(args) -> int:
    settings = get_settings()
    print(
        json.dumps(
            {
                "product": settings.product,
                "version": settings.version,
                "gateway_protocol_version": settings.gateway_protocol_version,
                "app_env": settings.app_env,
                "database_url_scheme": settings.database_url.split(":", 1)[0],
                "provider_configured": settings.runpod_configured,
                "max_hourly_price": str(settings.max_hourly_price),
                "max_session_budget": str(settings.max_session_budget),
            },
            ensure_ascii=False,
        )
    )
    return 0


def pending_codes(args) -> int:
    with _db()() as db:
        rows = db.scalars(select(EnrollmentCode).order_by(EnrollmentCode.created_at.desc())).all()
        for row in rows:
            state = "used" if row.redeemed_at else "revoked" if row.revoked_at else "open"
            print(
                f"{row.code_hash[:16]}…  {state:8s} label={row.label or '-':<20.20s} "
                f"expires={utc(row.expires_at).isoformat()}"
            )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gateway", description="Alex Cloud Gateway operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-code", help="create a one-time installation activation code")
    create.add_argument("--label", default="")
    create.add_argument("--ttl", type=int, default=0, help="minutes; defaults to ACTIVATION_TTL_MINUTES")
    create.add_argument("--code", default="", help="fixed code (tests/dev only)")
    create.set_defaults(func=create_code)

    listing = sub.add_parser("installations", help="list installations and the compute state")
    listing.set_defaults(func=list_installations)

    codes = sub.add_parser("codes", help="list activation codes (digests only)")
    codes.set_defaults(func=pending_codes)

    revoke_cmd = sub.add_parser("revoke", help="revoke one installation")
    revoke_cmd.add_argument("--installation-id", required=True)
    revoke_cmd.add_argument("--reason", default="")
    revoke_cmd.set_defaults(func=revoke)

    audit_cmd = sub.add_parser("audit", help="recent audit events")
    audit_cmd.add_argument("--limit", type=int, default=20)
    audit_cmd.set_defaults(func=audit)

    reset = sub.add_parser("reset-create-budget", help="clear an unresolved create after manual review")
    reset.set_defaults(func=reset_create_budget)

    health_cmd = sub.add_parser("health", help="local configuration check (no secrets printed)")
    health_cmd.set_defaults(func=health)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
