import argparse
import contextlib
import json
import logging
import os
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


def _prompt(value, prompt_text):
    if value:
        return value
    try:
        return input(prompt_text)
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)


def _bootstrap_odoo(config_path, database):
    import odoo

    odoo_args = []
    if config_path:
        odoo_args.extend(["-c", config_path])
    if database:
        odoo_args.extend(["-d", database])
    odoo.tools.config.parse_config(odoo_args)
    odoo.service.server.load_server_wide_modules_and_middlewares()
    registry = odoo.modules.registry.Registry.new(database)
    cr = registry.cursor()
    return odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})


@contextlib.contextmanager
def _managed_env(config_path, database):
    env = _bootstrap_odoo(config_path, database)
    try:
        yield env
    finally:
        env.cr.close()


def _env_from_args(args):
    database = _prompt(args.database, "Odoo database name: ")
    config_path = args.config or os.environ.get("ODOO_RC")
    return _managed_env(config_path, database)


def _read_config(env, args):
    icp = env["ir.config_parameter"].sudo()
    api_token = (
        args.api_token
        or os.environ.get("ODOOINDEX_API_TOKEN")
        or icp.get_param("odooindex_connector.api_token")
        or ""
    )
    target_version = (
        args.target_version
        or os.environ.get("ODOOINDEX_TARGET_VERSION")
        or icp.get_param("odooindex_connector.target_version")
        or ""
    )
    if not target_version:
        _error(
            "Target Odoo version is required (set in Odoo, use --target-version, "
            "or set ODOOINDEX_TARGET_VERSION)."
        )
    instance_name = (
        args.instance_name
        or os.environ.get("ODOOINDEX_INSTANCE_NAME")
        or icp.get_param("odooindex_connector.instance_name")
        or env.cr.dbname
    )
    base_url = (
        args.base_url
        or os.environ.get("ODOOINDEX_BASE_URL")
        or icp.get_param("odooindex_connector.base_url")
        or DEFAULT_API_URL
    )
    return ConnectorConfig(
        api_token=api_token,
        target_version=target_version,
        instance_name=instance_name,
        base_url=base_url,
    )


def _get_uuid(env):
    uuid = env["ir.config_parameter"].sudo().get_param("database.uuid")
    if not uuid:
        _error("Database UUID not found.")
    return uuid


def _get_modules(env):
    return env["ir.module.module"].sudo().search([("state", "=", "installed")])


def _get_odoo_version(env):
    import odoo

    return odoo.release.series


def _pair(env, args, config):
    uuid = _get_uuid(env)
    client = OdooIndexClient(base_url=config.base_url)
    pairing = PairingService(client)
    pairing_id = args.pairing_id
    pairing_secret = args.pairing_secret
    if not pairing_id or not pairing_secret:
        result = pairing.start(uuid, config.instance_name)
        pairing_id = result.get("pairing_id")
        pairing_secret = result.get("pairing_secret")
        pairing_url = result.get("pairing_url")
        if not pairing_id or not pairing_secret:
            _error("Pairing start failed.")
        print(f"Open this URL to sign in: {pairing_url}")
    pin = _prompt(args.pin, "Enter pairing PIN: ")
    if not pin:
        _error("PIN is required.")
    result = pairing.verify(pairing_id, pairing_secret, pin)
    token = result.get("api_token")
    if not token:
        _error("Pairing verification failed.")
    env["ir.config_parameter"].sudo().set_param("odooindex_connector.api_token", token)
    env["ir.config_parameter"].sudo().set_param(
        "odooindex_connector.instance_name", config.instance_name
    )
    env.cr.commit()
    config.api_token = token
    return config


def _ensure_token(env, args):
    config = _read_config(env, args)
    if config.api_token:
        return config
    return _pair(env, args, config)


def _build_report(modules, updates, config):
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
    return report


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


def _run(env, args):
    config = _ensure_token(env, args)
    uuid = _get_uuid(env)
    modules = _get_modules(env)
    odoo_version = _get_odoo_version(env)
    payload = build_inventory_payload(
        uuid=uuid,
        instance_name=config.instance_name,
        odoo_version=odoo_version,
        target_version=config.target_version,
        modules=modules,
    )
    client = OdooIndexClient(api_token=config.api_token, base_url=config.base_url)
    service = SyncService(client)
    service.upload_inventory(payload)
    response = service.download_updates(uuid, config.target_version)
    updates = response.get("updates", []) if isinstance(response, dict) else response
    report = _build_report(modules, updates, config)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_readiness(report)


def main():
    parser = argparse.ArgumentParser(prog="clox")
    parser.add_argument(
        "-c",
        "--config",
        default=os.environ.get("ODOO_RC"),
        help="Odoo configuration file (default: ODOO_RC env)",
    )
    parser.add_argument(
        "-d",
        "--database",
        default=os.environ.get("ODOO_DATABASE"),
        help="Odoo database name",
    )
    parser.add_argument(
        "--api-token",
        default=os.environ.get("ODOOINDEX_API_TOKEN"),
        help="OdooIndex API token",
    )
    parser.add_argument(
        "--target-version",
        default=os.environ.get("ODOOINDEX_TARGET_VERSION"),
        help="Target Odoo version",
    )
    parser.add_argument(
        "--instance-name",
        default=os.environ.get("ODOOINDEX_INSTANCE_NAME"),
        help="Display name for this instance",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ODOOINDEX_BASE_URL", DEFAULT_API_URL),
        help="OdooIndex API base URL",
    )
    parser.add_argument("--pairing-id", help="Pairing ID")
    parser.add_argument("--pairing-secret", help="Pairing secret")
    parser.add_argument("--pin", help="Pairing PIN")
    parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        with _env_from_args(args) as env:
            _run(env, args)
    except OdooIndexError as exc:
        _error(str(exc))
    except Exception as exc:
        _logger.exception("Unexpected error")
        _error("%s", exc)


if __name__ == "__main__":
    main()
