"""Connecteurs de stockage cloud (SharePoint, OneDrive, pCloud, Google Drive).

Gère l'import/export de fichiers CSV depuis/vers les services cloud.
Utilise les API REST ou la détection de chemins locaux synchronisés.

Stratégie :
1. Priorité au chemin local synchronisé (si le client de sync est installé)
2. Fallback sur l'API REST si disponible
3. Les credentials sont stockés dans ~/.csvtraitement/cloud_config.json
"""

import json
import logging
import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / '.csvtraitement'
CLOUD_CONFIG_FILE = CONFIG_DIR / 'cloud_config.json'


class CloudProvider:
    """Fournisseurs de stockage cloud supportés."""
    SHAREPOINT = 'sharepoint'
    ONEDRIVE = 'onedrive'
    PCLOUD = 'pcloud'
    GOOGLE_DRIVE = 'google_drive'
    DROPBOX = 'dropbox'
    LOCAL = 'local'


@dataclass
class CloudLocation:
    """Emplacement cloud détecté ou configuré."""
    provider: str
    name: str
    local_path: Optional[Path] = None     # Chemin local synchronisé
    remote_url: Optional[str] = None      # URL de l'API
    is_available: bool = False
    is_sync_client: bool = False          # Via client de synchronisation


@dataclass
class CloudConfig:
    """Configuration des connexions cloud."""
    locations: list[CloudLocation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'locations': [
                {
                    'provider': loc.provider,
                    'name': loc.name,
                    'local_path': str(loc.local_path) if loc.local_path else None,
                    'remote_url': loc.remote_url,
                }
                for loc in self.locations
            ]
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CloudConfig':
        config = cls()
        for loc_data in data.get('locations', []):
            loc = CloudLocation(
                provider=loc_data['provider'],
                name=loc_data['name'],
                local_path=Path(loc_data['local_path']) if loc_data.get('local_path') else None,
                remote_url=loc_data.get('remote_url'),
            )
            if loc.local_path and loc.local_path.exists():
                loc.is_available = True
                loc.is_sync_client = True
            config.locations.append(loc)
        return config


def _detect_windows_paths() -> list[CloudLocation]:
    """Détecte les chemins de synchronisation cloud sur Windows."""
    locations = []
    home = Path.home()

    # OneDrive
    for name in ['OneDrive', 'OneDrive - Personnel', 'OneDrive - Business']:
        path = home / name
        if path.exists():
            locations.append(CloudLocation(
                provider=CloudProvider.ONEDRIVE,
                name=name,
                local_path=path,
                is_available=True,
                is_sync_client=True,
            ))

    # OneDrive via variable d'environnement
    onedrive_env = os.environ.get('OneDrive') or os.environ.get('OneDriveCommercial')
    if onedrive_env:
        path = Path(onedrive_env)
        if path.exists() and not any(loc.local_path == path for loc in locations):
            locations.append(CloudLocation(
                provider=CloudProvider.ONEDRIVE,
                name=f"OneDrive ({path.name})",
                local_path=path,
                is_available=True,
                is_sync_client=True,
            ))

    # SharePoint (apparaît comme dossier OneDrive avec le nom de l'entreprise)
    for item in home.iterdir():
        if item.is_dir() and item.name.startswith('OneDrive -') and 'Personnel' not in item.name:
            if not any(loc.local_path == item for loc in locations):
                locations.append(CloudLocation(
                    provider=CloudProvider.SHAREPOINT,
                    name=item.name.replace('OneDrive - ', ''),
                    local_path=item,
                    is_available=True,
                    is_sync_client=True,
                ))

    # pCloud
    for name in ['pCloudDrive', 'pCloud Drive']:
        for drive_letter in 'DEFGHIJKLMNOPQRSTUVWXYZ':
            path = Path(f'{drive_letter}:/')
            if path.exists() and (path / name).exists():
                locations.append(CloudLocation(
                    provider=CloudProvider.PCLOUD,
                    name='pCloud',
                    local_path=path / name,
                    is_available=True,
                    is_sync_client=True,
                ))
                break
        pcloud_local = home / 'pCloudDrive'
        if pcloud_local.exists():
            locations.append(CloudLocation(
                provider=CloudProvider.PCLOUD,
                name='pCloud',
                local_path=pcloud_local,
                is_available=True,
                is_sync_client=True,
            ))
            break

    # Google Drive
    for name in ['Google Drive', 'Mon Drive', 'My Drive']:
        path = home / name
        if path.exists():
            locations.append(CloudLocation(
                provider=CloudProvider.GOOGLE_DRIVE,
                name='Google Drive',
                local_path=path,
                is_available=True,
                is_sync_client=True,
            ))
            break
    # Google Drive Stream
    for drive_letter in 'GHIJKLMNOPQRSTUVWXYZ':
        gd_path = Path(f'{drive_letter}:/Mon Drive')
        gd_path2 = Path(f'{drive_letter}:/My Drive')
        for p in [gd_path, gd_path2]:
            if p.exists():
                locations.append(CloudLocation(
                    provider=CloudProvider.GOOGLE_DRIVE,
                    name=f'Google Drive ({drive_letter}:)',
                    local_path=p,
                    is_available=True,
                    is_sync_client=True,
                ))
                break

    # Dropbox
    dropbox_path = home / 'Dropbox'
    if dropbox_path.exists():
        locations.append(CloudLocation(
            provider=CloudProvider.DROPBOX,
            name='Dropbox',
            local_path=dropbox_path,
            is_available=True,
            is_sync_client=True,
        ))

    return locations


def _detect_linux_paths() -> list[CloudLocation]:
    """Détecte les chemins de synchronisation cloud sur Linux."""
    locations = []
    home = Path.home()

    # pCloud (FUSE mount)
    for path in [home / 'pCloudDrive', Path('/mnt/pcloud')]:
        if path.exists():
            locations.append(CloudLocation(
                provider=CloudProvider.PCLOUD,
                name='pCloud',
                local_path=path,
                is_available=True,
                is_sync_client=True,
            ))
            break

    # Google Drive (via rclone ou google-drive-ocamlfuse)
    for path in [home / 'google-drive', home / 'Google Drive', home / 'gdrive']:
        if path.exists():
            locations.append(CloudLocation(
                provider=CloudProvider.GOOGLE_DRIVE,
                name='Google Drive',
                local_path=path,
                is_available=True,
                is_sync_client=True,
            ))
            break

    # Dropbox
    dropbox_path = home / 'Dropbox'
    if dropbox_path.exists():
        locations.append(CloudLocation(
            provider=CloudProvider.DROPBOX,
            name='Dropbox',
            local_path=dropbox_path,
            is_available=True,
            is_sync_client=True,
        ))

    return locations


def _detect_macos_paths() -> list[CloudLocation]:
    """Détecte les chemins de synchronisation cloud sur macOS."""
    locations = []
    home = Path.home()

    # OneDrive
    for name in ['OneDrive', 'OneDrive - Personnel']:
        path = home / name
        if path.exists():
            locations.append(CloudLocation(
                provider=CloudProvider.ONEDRIVE,
                name=name,
                local_path=path,
                is_available=True,
                is_sync_client=True,
            ))

    # Google Drive
    gd_path = home / 'Library' / 'CloudStorage' / 'GoogleDrive'
    if gd_path.exists():
        locations.append(CloudLocation(
            provider=CloudProvider.GOOGLE_DRIVE,
            name='Google Drive',
            local_path=gd_path,
            is_available=True,
            is_sync_client=True,
        ))

    # Dropbox
    dropbox_path = home / 'Dropbox'
    if dropbox_path.exists():
        locations.append(CloudLocation(
            provider=CloudProvider.DROPBOX,
            name='Dropbox',
            local_path=dropbox_path,
            is_available=True,
            is_sync_client=True,
        ))

    # pCloud
    pcloud_path = home / 'pCloudDrive'
    if pcloud_path.exists():
        locations.append(CloudLocation(
            provider=CloudProvider.PCLOUD,
            name='pCloud',
            local_path=pcloud_path,
            is_available=True,
            is_sync_client=True,
        ))

    return locations


def detect_cloud_locations() -> list[CloudLocation]:
    """Détecte automatiquement les emplacements cloud disponibles.

    Returns:
        Liste des emplacements cloud détectés.
    """
    system = platform.system()

    if system == 'Windows':
        locations = _detect_windows_paths()
    elif system == 'Darwin':
        locations = _detect_macos_paths()
    else:
        locations = _detect_linux_paths()

    # Dédupliquer par chemin
    seen_paths = set()
    unique = []
    for loc in locations:
        key = str(loc.local_path) if loc.local_path else loc.name
        if key not in seen_paths:
            seen_paths.add(key)
            unique.append(loc)

    logger.info(f"Cloud détecté: {len(unique)} emplacements ({[l.name for l in unique]})")
    return unique


def save_cloud_config(config: CloudConfig):
    """Sauvegarde la configuration cloud."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CLOUD_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
    logger.info(f"Configuration cloud sauvegardée: {CLOUD_CONFIG_FILE}")


def load_cloud_config() -> CloudConfig:
    """Charge la configuration cloud."""
    if CLOUD_CONFIG_FILE.exists():
        with open(CLOUD_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return CloudConfig.from_dict(data)
    return CloudConfig()


def list_csv_files(location: CloudLocation, pattern: str = '*.csv') -> list[Path]:
    """Liste les fichiers CSV dans un emplacement cloud.

    Args:
        location: Emplacement cloud.
        pattern: Pattern glob.

    Returns:
        Liste de chemins de fichiers.
    """
    if not location.local_path or not location.local_path.exists():
        return []

    files = sorted(location.local_path.rglob(pattern))
    return files


def copy_from_cloud(
    source: Path,
    destination: Path
) -> Path:
    """Copie un fichier depuis le cloud vers un emplacement local.

    Args:
        source: Chemin source (peut être cloud/réseau).
        destination: Chemin de destination local.

    Returns:
        Chemin du fichier copié.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(destination))
    logger.info(f"Copié: {source} → {destination}")
    return destination


def copy_to_cloud(
    source: Path,
    destination: Path
) -> Path:
    """Copie un fichier local vers un emplacement cloud.

    Args:
        source: Chemin source local.
        destination: Chemin de destination (cloud/réseau).

    Returns:
        Chemin du fichier copié.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(destination))
    logger.info(f"Copié vers cloud: {source} → {destination}")
    return destination
