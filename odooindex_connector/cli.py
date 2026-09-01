import argparse
import contextlib
import json
import logging
import sys

from .core import (
    DEFAULT_API_URL,
    ConnectorConfig,
    OdooIndexClient,
    OdooIndexError,
    PairingService,
    SyncService,
    build_inventory_payload,
    module_to_dict,
)

_logger = logging.getLogger(__name__)

# The CLI is a terminal tool: print is intentional here.
# pylint: disable=print-used


def _error(message, *args):
    msg = message % args if args else message
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _bootstrap_odoo(config_path, database):
    import odoo

    odoo_args = []
    if config_path:
        odoo_args.extend(["-c", config_path])
    odoo.tools.config.parse_config(odoo_args)
    odoo.service.server.load_server_wide_modules_and_middlewares()
    registry = odoo.modules.registry.Registry.new(database)
    cr = registry.cursor()
    return odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})


@contextlib.contextmanager
def _managed_env(args):
    if not args.database:
        yield None
        return
    env = _bootstrap_odoo(args.config, args.database)
    try:
        yield env
    finally:
        env.cr.close()


def _get_config(env, args):
    if env:
        icp = env["ir.config_parameter"].sudo()
        api_token = (
            args.api_token or icp.get_param("odooindex_connector.api_token") or ""
        )
        target_version = (
            args.target_version
            or icp.get_param("odooindex_connector.target_version")
            or ""
        )
        instance_name = (
            args.instance_name
            or icp.get_param("odooindex_connector.instance_name")
            or env.cr.dbname
        )
    else:
        api_token = args.api_token or ""
        target_version = args.target_version or ""
        instance_name = args.instance_name or ""
    return ConnectorConfig(
        api_token=api_token,
        target_version=target_version,
        instance_name=instance_name,
        base_url=args.base_url or DEFAULT_API_URL,
    )


def _get_uuid(env, args):
    if env:
        uuid = env["ir.config_parameter"].sudo().get_param("database.uuid")
    else:
        uuid = args.uuid
    if not uuid:
        _error("Database UUID is required (use --uuid or --database).")
    return uuid


def _get_instance_name(env, args):
    if args.instance_name:
        return args.instance_name
    if env:
        return (
            env["ir.config_parameter"]
            .sudo()
            .get_param("odooindex_connector.instance_name")
            or env.cr.dbname
        )
    return ""


def _get_modules(env, args):
    if env:
        return env["ir.module.module"].sudo().search([("state", "=", "installed")])
    if args.modules_file:
        with open(args.modules_file, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data.get("modules", [])
            return data
    return []


def _get_odoo_version(env, args):
    if args.odoo_version:
        return args.odoo_version
    if env:
        import odoo

        return odoo.release.series
    return ""


def _print_readiness(report):
    print(
        f"{'module':<30} {'installed':<16} {'latest':<16} "
        f"{'target':<8} {'status':<12} {'update':<6}"
    )
    for row in report:
        print(
            f"{row['name']:<30} "
            f"{row['installed_version']:<16} "
            f"{row['latest_version']:<16} "
            f"{row['target_version']:<8} "
            f"{row['migration_status']:<12} "
            f"{'yes' if row['has_update'] else 'no':<6}"
        )


def _run_upload(env, args):
    config = _get_config(env, args)
    if not config.api_token:
        _error("API token is required.")
    uuid = _get_uuid(env, args)
    modules = _get_modules(env, args)
    odoo_version = _get_odoo_version(env, args)
    payload = build_inventory_payload(
        uuid=uuid,
        instance_name=config.instance_name,
        odoo_version=odoo_version,
        target_version=config.target_version,
        modules=modules,
    )
    client = OdooIndexClient(api_token=config.api_token, base_url=config.base_url)
    result = SyncService(client).upload_inventory(payload)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Uploaded {len(payload['modules'])} module(s).")


def _run_download(env, args):
    config = _get_config(env, args)
    if not config.api_token:
        _error("API token is required.")
    uuid = _get_uuid(env, args)
    client = OdooIndexClient(api_token=config.api_token, base_url=config.base_url)
    result = SyncService(client).download_updates(uuid, config.target_version)
    print(json.dumps(result, indent=2))


def _run_sync(env, args):
    config = _get_config(env, args)
    if not config.api_token:
        _error("API token is required.")
    uuid = _get_uuid(env, args)
    modules = _get_modules(env, args)
    odoo_version = _get_odoo_version(env, args)
    payload = build_inventory_payload(
        uuid=uuid,
        instance_name=config.instance_name,
        odoo_version=odoo_version,
        target_version=config.target_version,
        modules=modules,
    )
    client = OdooIndexClient(api_token=config.api_token, base_url=config.base_url)
    service = SyncService(client)
    upload_result = service.upload_inventory(payload)
    updates = service.download_updates(uuid, config.target_version)
    if env:
        env["ir.module.module"].sudo()._apply_updates(updates)
        env.cr.commit()
    if args.json:
        print(json.dumps({"upload": upload_result, "updates": updates}, indent=2))
    else:
        update_count = (
            len(updates.get("updates", []))
            if isinstance(updates, dict)
            else len(updates)
        )
        print(
            f"Sync complete. Uploaded {len(payload['modules'])} module(s). "
            f"Received {update_count} update(s)."
        )


def _run_readiness(env, args):
    config = _get_config(env, args)
    if not config.api_token:
        _error("API token is required.")
    uuid = _get_uuid(env, args)
    modules = _get_modules(env, args)
    client = OdooIndexClient(api_token=config.api_token, base_url=config.base_url)
    response = SyncService(client).download_updates(uuid, config.target_version)
    updates = response.get("updates", []) if isinstance(response, dict) else response
    updates_by_name = {u.get("name"): u for u in updates if u.get("name")}

    report = []
    for module in modules:
        info = module_to_dict(module)
        name = info["name"]
        update = updates_by_name.get(name, {})
        latest = update.get("latest_version", "")
        installed = info["version"]
        has_update = bool(latest and installed and latest != installed)
        report.append(
            {
                "name": name,
                "installed_version": installed,
                "latest_version": latest,
                "target_version": update.get("target_version", config.target_version),
                "migration_status": update.get("migration_status", "unknown"),
                "pr_count": len(update.get("pull_requests", [])),
                "has_update": has_update,
            }
        )
    if not modules:
        for update in updates:
            report.append(
                {
                    "name": update.get("name", ""),
                    "installed_version": "",
                    "latest_version": update.get("latest_version", ""),
                    "target_version": update.get(
                        "target_version", config.target_version
                    ),
                    "migration_status": update.get("migration_status", "unknown"),
                    "pr_count": len(update.get("pull_requests", [])),
                    "has_update": False,
                }
            )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_readiness(report)


def _run_pair(env, args):
    uuid = _get_uuid(env, args)
    instance_name = _get_instance_name(env, args)
    client = OdooIndexClient(base_url=args.base_url or DEFAULT_API_URL)
    result = PairingService(client).start(uuid, instance_name)
    if env:
        env["ir.config_parameter"].sudo().set_param(
            "odooindex_connector.instance_name", instance_name
        )
        env.cr.commit()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Open this URL in a browser to sign in:\n  {result.get('pairing_url')}")
        print(
            "Then run:\n"
            f"  odooindex-connector pair-verify "
            f"--pairing-id {result.get('pairing_id')} "
            f"--pairing-secret {result.get('pairing_secret')} "
            f"--pin <PIN>"
        )


def _run_pair_verify(env, args):
    client = OdooIndexClient(base_url=args.base_url or DEFAULT_API_URL)
    result = PairingService(client).verify(
        args.pairing_id, args.pairing_secret, args.pin
    )
    token = result.get("api_token")
    if not token:
        _error("Pairing verification failed.")
    if env:
        env["ir.config_parameter"].sudo().set_param(
            "odooindex_connector.api_token", token
        )
        env.cr.commit()
    if args.json:
        print(
            json.dumps({"api_token": token, "status": result.get("status")}, indent=2)
        )
    else:
        print("Pairing successful." if env else f"API token: {token}")


def main():
    parser = argparse.ArgumentParser(prog="odooindex-connector")
    parser.add_argument("-c", "--config", help="Odoo configuration file")
    parser.add_argument("-d", "--database", help="Odoo database name")
    parser.add_argument("--api-token", help="OdooIndex API token")
    parser.add_argument("--target-version", help="Target Odoo version")
    parser.add_argument("--instance-name", help="Display name for this instance")
    parser.add_argument(
        "--base-url", default=DEFAULT_API_URL, help="OdooIndex API base URL"
    )
    parser.add_argument("--uuid", help="Database UUID")
    parser.add_argument(
        "--modules-file",
        help="JSON file with installed module metadata",
    )
    parser.add_argument(
        "--odoo-version", help="Current Odoo version (default: read from Odoo)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    subparsers = parser.add_subparsers(dest="command", title="commands")
    subparsers.add_parser("upload", help="Upload module inventory")
    subparsers.add_parser("download", help="Download migration updates")
    subparsers.add_parser("sync", help="Upload and download updates")
    subparsers.add_parser("readiness", help="Check migration readiness")
    subparsers.add_parser("pair", help="Start device pairing")
    pair_verify = subparsers.add_parser("pair-verify", help="Verify pairing PIN")
    pair_verify.add_argument("--pairing-id", required=True)
    pair_verify.add_argument("--pairing-secret", required=True)
    pair_verify.add_argument("--pin", required=True)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        with _managed_env(args) as env:
            if args.command == "upload":
                _run_upload(env, args)
            elif args.command == "download":
                _run_download(env, args)
            elif args.command == "sync":
                _run_sync(env, args)
            elif args.command == "readiness":
                _run_readiness(env, args)
            elif args.command == "pair":
                _run_pair(env, args)
            elif args.command == "pair-verify":
                _run_pair_verify(env, args)
    except OdooIndexError as exc:
        _error(str(exc))
    except Exception as exc:
        _logger.exception("Unexpected error")
        _error("%s", exc)


if __name__ == "__main__":
    main()
