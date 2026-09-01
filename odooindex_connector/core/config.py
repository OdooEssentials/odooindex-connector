from dataclasses import dataclass

from .client import DEFAULT_API_URL


@dataclass
class ConnectorConfig:
    api_token: str = ""
    target_version: str = ""
    instance_name: str = ""
    base_url: str = DEFAULT_API_URL
