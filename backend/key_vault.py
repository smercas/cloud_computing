from typing import Any, Callable, overload
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient, KeyVaultSecret

from . import utils

class KeyVault:
  type Transform[R] = Callable[[KeyVaultSecret], R]
  def __init__(self, name: str="cloud-kv0", default_transform: Transform[Any]=utils.identity):
    self.__name = name
    self.__uri = f"https://{self.__name}.vault.azure.net/"
    self.__credential = DefaultAzureCredential()
    self.__client = SecretClient(vault_url=self.__uri, credential=self.__credential)
    self.__default_transform = default_transform

  @overload
  def __getitem__(self, k: str) -> KeyVaultSecret: ...
  @overload
  def __getitem__[TR](self, k: tuple[str | Transform[TR]]) -> TR: ...

  def __getitem__[TR](self, k) -> TR:
    match k:
      case str() as secret_name:
        r = self.get(secret_name)
      case (str() as secret_name, transform) if isinstance(transform, Callable):
        r = self.get(secret_name, transform=transform)
      case _: raise TypeError(f"Invalid key type for __getitem__: {k!r} (supported key types: (str), (str, Callable[[KeyVaultSecret], TR]))")
    if r is not None:
      return r
    else:
      raise KeyError(secret_name)

  def get[D, TR](self, secret_name: str, default: D=None, transform: Transform[TR]=None) -> TR | D:
    if transform is None: transform = self.default_transform
    try:
      return transform(self.__client.get_secret(secret_name))
    except Exception:
      return default
  @property
  def default_transform(self) -> Transform[Any]: return self.__default_transform
  @default_transform.setter
  def default_transform(self, t: Transform[Any]) -> None: self.__default_transform = t
