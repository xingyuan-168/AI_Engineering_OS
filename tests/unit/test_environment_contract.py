from pathlib import Path

import pytest

from codex_ai_os.domain.environment import (
    EnvironmentContract,
    EnvironmentServiceSpec,
    PersistentMountSpec,
    SharedAssetSpec,
)
from codex_ai_os.infrastructure.config import ConfigError, load_environment_contract


def test_environment_contract_loads_strict_oci_first_config(tmp_path: Path) -> None:
    config_dir = tmp_path / ".codex-os"
    config_dir.mkdir()
    (config_dir / "environment.yaml").write_text(
        "schema_version: '1.2'\n"
        "environment_mode: oci-first\n"
        "oci_backend: docker\n"
        "compose_files: [compose.yaml]\n"
        "dockerfiles: [docker/app.Dockerfile]\n"
        "dependency_locks: [uv.lock]\n"
        "services:\n"
        "  - name: app\n"
        "    dockerfile: docker/app.Dockerfile\n"
        "persistent_mounts: []\n"
        "shared_assets: []\n"
        "host_budget: {}\n",
        encoding="utf-8",
    )

    contract = load_environment_contract(tmp_path)

    assert contract.oci_backend.value == "docker"
    assert contract.services[0].name == "app"


@pytest.mark.parametrize("path", ["../Dockerfile", "/Dockerfile", "C:/Dockerfile"])
def test_environment_contract_rejects_escaping_paths(path: str) -> None:
    with pytest.raises(ValueError, match="project-relative"):
        EnvironmentContract(dockerfiles=(path,))


def test_environment_contract_requires_stateful_recovery() -> None:
    with pytest.raises(ValueError, match="backup/restore"):
        EnvironmentServiceSpec(
            name="db",
            dockerfile="docker/db.Dockerfile",
            stateful=True,
            persistent_targets=("/var/lib/db",),
        )


def test_environment_contract_rejects_unlisted_service_dockerfile() -> None:
    with pytest.raises(ValueError, match="listed"):
        EnvironmentContract(
            services=(
                EnvironmentServiceSpec(
                    name="app", dockerfile="docker/app.Dockerfile"
                ),
            )
        )


def test_environment_contract_validates_persistence_and_shared_assets() -> None:
    contract = EnvironmentContract(
        dockerfiles=("docker/db.Dockerfile",),
        services=(
            EnvironmentServiceSpec(
                name="db",
                dockerfile="docker/db.Dockerfile",
                stateful=True,
                persistent_targets=("/var/lib/db",),
                backup_command=("db-backup",),
                restore_command=("db-restore",),
            ),
        ),
        persistent_mounts=(
            PersistentMountSpec(service="db", source="db-data", target="/var/lib/db"),
        ),
        shared_assets=(
            SharedAssetSpec(
                name="model", source_env="CODEX_SHARED_MODELS", target="/models"
            ),
        ),
    )

    assert contract.shared_assets[0].read_only


def test_missing_environment_contract_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load_environment_contract(tmp_path)
