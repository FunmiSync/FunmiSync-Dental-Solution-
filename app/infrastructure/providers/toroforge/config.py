from dataclasses import dataclass 
from app.config import settings

@dataclass(frozen=True)
class ToroforgeConfig:
     network: str
     base_url: str
     connectw_url: str
     payment_url: str
     deployer_url: str
     admin: str
     adminpwd: str
     timeout_seconds: float


def get_toroforge_config() -> ToroforgeConfig:
     return ToroforgeConfig(
          network = settings.toroforge_network,
          base_url = settings.toroforge_base_url,
          connectw_url= settings.toroforge_connectw_url,
          payment_url= settings.toroforge_payment_url,
          deployer_url = settings.toroforge_deployer_url,
          admin = settings.toroforge_admin,
          adminpwd = settings.toroforge_adminpwd,
          timeout_seconds = 15.0
     )

