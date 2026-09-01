import argparse
import configparser
import contextlib
import importlib
import json
import logging
import os
import re
import sys
import urllib.parse

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
    print(prompt_text, end="", file=sys.stderr)
    try:
        return sys.stdin.readline().rstrip("\n")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)


def _secure_base_url(base_url):
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"https", "http"} or (
        parsed.scheme == "http"
        and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise OdooIndexError(
            "Base URL scheme must be HTTPS, or HTTP for localhost only."
        )
    return base_url


class _Database:
    def __init__(self, config_path, database):
        self.config_path = config_path
        self.database = database
        self._conn = None

    def _dbapi(self):
        for name in ("psycopg2", "psycopg"):
            try:
                return importlib.import_module(name)
            except ImportError:
                continue
        raise OdooIndexError(
            "A PostgreSQL driver is required. Install psycopg or psycopg2."
        )

    def _connection_params(self):
        params = {"dbname": self.database}
        cfg = configparser.ConfigParser(interpolation=None)
        if self.config_path and os.path.isfile(self.config_path):
            cfg.read(self.config_path)
        for option, param in (
            ("db_host", "host"),
            ("db_port", "port"),
            ("db_user", "user"),
            ("db_password", "password"),
            ("db_sslmode", "sslmode"),
        ):
            if not cfg.has_option("options", option):
                continue
            value = cfg.get("options", option)
            if not value or value.strip().lower() in {"false", "none"}:
                continue
            if param == "port":
                try:
                    params[param] = int(value)
                except ValueError as exc:
                    raise OdooIndexError(
                        f"Invalid integer for {option}: {value}"
                    ) from exc
            else:
                params[param] = value
        return params

    def _connect(self):
        if self._conn is None:
            self._conn = self._dbapi().connect(**self._connection_params())
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def get_param(self, key, default=None):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (key,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else default

    def set_param(self, key, value):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO ir_config_parameter "
                "(key, value, create_uid, write_uid, create_date, write_date) "
                "VALUES (%s, %s, 1, 1, now() at time zone 'utc', "
                "now() at time zone 'utc') "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                "write_uid = EXCLUDED.write_uid, write_date = EXCLUDED.write_date",
                (key, value),
            )
            self._signal_cache_invalidation(cur)
            conn.commit()
        finally:
            cur.close()

    def _signal_cache_invalidation(self, cur):
        try:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'orm_signaling_default' "
                "AND table_schema = current_schema()"
            )
            if cur.fetchone():
                cur.execute("INSERT INTO orm_signaling_default DEFAULT VALUES")
                return
            cur.execute("SELECT nextval('base_cache_signaling')")
        except self._dbapi().Error as exc:
            _logger.debug("Could not signal cache invalidation: %s", exc)

    def get_modules(self):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT name, COALESCE(shortdesc, '') AS shortdesc, "
            "COALESCE(latest_version, '') AS latest_version, "
            "COALESCE(author, '') AS author, "
            "COALESCE(website, '') AS website, "
            "COALESCE(license, '') AS license "
            "FROM ir_module_module WHERE state = 'installed' "
            "AND name IS NOT NULL"
        )
        columns = [desc[0] for desc in cur.description]
        modules = [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]
        cur.close()
        return modules

    def get_odoo_version(self):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(latest_version, '') FROM ir_module_module "
            "WHERE name = 'base' LIMIT 1"
        )
        row = cur.fetchone()
        cur.close()
        version = row[0] if row else ""
        match = re.match(r"(\d+\.\d+)", version)
        return match.group(1) if match else ""


@contextlib.contextmanager
def _open_db(config_path, database):
    db = _Database(config_path, database)
    try:
        yield db
    finally:
        db.close()


def _read_config(db, args):
    api_token = (
        args.api_token
        or os.environ.get("ODOOINDEX_API_TOKEN")
        or db.get_param("odooindex_connector.api_token")
        or ""
    )
    target_version = (
        args.target_version
        or os.environ.get("ODOOINDEX_TARGET_VERSION")
        or db.get_param("odooindex_connector.target_version")
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
        or db.get_param("odooindex_connector.instance_name")
        or db.database
    )
    base_url = (
        args.base_url
        or os.environ.get("ODOOINDEX_BASE_URL")
        or db.get_param("odooindex_connector.base_url")
        or DEFAULT_API_URL
    )
    _secure_base_url(base_url)
    return ConnectorConfig(
        api_token=api_token,
        target_version=target_version,
        instance_name=instance_name,
        base_url=base_url,
    )


def _get_uuid(db):
    uuid = db.get_param("database.uuid")
    if not uuid:
        _error("Database UUID not found.")
    return uuid


def _pair(db, args, config):
    uuid = _get_uuid(db)
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
        print(f"Open this URL to sign in: {pairing_url}", file=sys.stderr)
    pin = _prompt(args.pin, "Enter pairing PIN: ")
    if not pin:
        _error("PIN is required.")
    result = pairing.verify(pairing_id, pairing_secret, pin)
    token = result.get("api_token")
    if not token:
        _error("Pairing verification failed.")
    db.set_param("odooindex_connector.api_token", token)
    db.set_param("odooindex_connector.instance_name", config.instance_name)
    config.api_token = token
    return config


def _ensure_token(db, args):
    config = _read_config(db, args)
    if config.api_token:
        return config
    return _pair(db, args, config)


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


def _run(db, args):
    config = _ensure_token(db, args)
    uuid = _get_uuid(db)
    modules = db.get_modules()
    odoo_version = db.get_odoo_version()
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
        database = _prompt(args.database, "Odoo database name: ")
        with _open_db(args.config, database) as db:
            _run(db, args)
    except OdooIndexError as exc:
        _error(str(exc))
    except Exception as exc:
        _logger.exception("Unexpected error")
        _error("%s", exc)


if __name__ == "__main__":
    main()
