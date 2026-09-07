"""Deployment guards for the always-on stack (ADR-0019).

These assert properties of `docker-compose.yml` that are invisible until the
day they matter: the host reboots and nothing comes back, the disk fills with
run logs, or the API turns out to be reachable from the LAN without a password.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

# Started and left running; the build-only entries are images, not services.
LONG_LIVED = ("api", "redis", "worker", "frontend", "frontend-prod")
BUILD_ONLY = ("runner", "sandbox")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


@pytest.fixture(scope="module")
def services(compose) -> dict:
    return compose["services"]


def test_every_long_lived_service_exists(services):
    assert set(LONG_LIVED) <= set(services)


@pytest.mark.parametrize("name", LONG_LIVED)
def test_long_lived_services_restart_with_the_host(services, name):
    assert services[name]["restart"] == "unless-stopped"


@pytest.mark.parametrize("name", BUILD_ONLY)
def test_build_only_services_are_not_restarted(services, name):
    """They are never started, so a restart policy would be a lie."""
    assert "restart" not in services[name]


@pytest.mark.parametrize("name", LONG_LIVED)
def test_long_lived_services_rotate_their_logs(services, name):
    """An always-on lab streaming run output fills the disk otherwise."""
    logging = services[name]["logging"]

    assert logging["driver"] == "json-file"
    assert logging["options"]["max-size"]
    assert int(logging["options"]["max-file"]) >= 2


def test_dev_and_prod_frontends_are_in_separate_profiles(services):
    assert services["frontend"]["profiles"] == ["dev"]
    assert services["frontend-prod"]["profiles"] == ["prod"]


def test_prod_ui_binds_to_loopback_unless_told_otherwise(services):
    """Exposing the UI to the LAN is opt-in, and pairs with `make set-password`."""
    (published,) = services["frontend-prod"]["ports"]

    assert published == "${HARNESS_LAB_BIND_ADDR:-127.0.0.1}:8080:80"


def test_api_bind_address_defaults_to_loopback(services):
    """Auth is off until a password is set, so LAN exposure must be opt-in.

    Publishing the dev API on every interface put an unauthenticated
    code-execution endpoint on the network for anyone who had not yet run
    `make set-password`.
    """
    (published,) = services["api"]["ports"]

    assert published == (
        "${HARNESS_LAB_API_BIND_ADDR:-127.0.0.1}:${HARNESS_LAB_API_PORT:-8009}:8008"
    )


def test_the_container_port_is_fixed_while_the_host_port_is_not(services):
    """Deploys collide on host ports; the compose network never does.

    Publishing on a different host port must not force nginx, the Vite proxy
    and the healthcheck to be retargeted too — so the container side of the
    mapping stays 8008 and only the host side reads an override.
    """
    (published,) = services["api"]["ports"]
    assert published.endswith(":8008")

    nginx = (REPO_ROOT / "deploy" / "nginx.conf").read_text()
    assert "server api:8008;" in nginx
    assert "8008/api/health" in str(services["api"]["healthcheck"]["test"])


def test_app_up_prod_pins_the_api_to_loopback():
    makefile = (REPO_ROOT / "Makefile").read_text()
    target = makefile.partition("\napp-up-prod:")[2].partition("\n\n")[0]

    assert "HARNESS_LAB_API_BIND_ADDR=127.0.0.1" in target
    assert "--profile prod" in target


def test_only_the_worker_holds_the_docker_socket(services):
    """ADR-0002: the API must never be able to launch a container."""
    holders = [
        name
        for name, service in services.items()
        if any("docker.sock" in volume for volume in service.get("volumes", []))
    ]

    assert holders == ["worker"]


def test_services_wait_for_their_dependencies_to_be_healthy(services):
    """Without a condition, compose starts them the instant the container exists."""
    for name in ("api", "worker", "frontend", "frontend-prod"):
        depends_on = services[name]["depends_on"]
        assert depends_on, name
        for dependency in depends_on.values():
            assert dependency["condition"] == "service_healthy", name


def test_the_prod_frontend_serves_the_api_on_one_origin():
    """Same origin is what makes the session cookie and Origin check work."""
    nginx = (REPO_ROOT / "deploy" / "nginx.conf").read_text()

    assert "location /api/" in nginx
    assert "proxy_pass http://harness_lab_api" in nginx
    # SPA fallback: client-side routes are not files on disk.
    assert "try_files $uri $uri/ /index.html" in nginx


def test_the_folder_picker_opens_beside_the_lab_by_default(services):
    """ADR-0045: picking a local folder is the first path the audit offers.

    It only works if the API can see the operator's repositories, so the mount
    defaults to the directory the lab itself sits in — where sibling checkouts
    usually are. The override moves that root; it must never be needed to turn
    the picker on.
    """
    api = services["api"]
    mounts = [volume for volume in api["volumes"] if "/host-sources" in volume]

    assert mounts == ["${HARNESS_LAB_LOCAL_SOURCES_DIR:-..}:/host-sources:ro"]
    # Read-only: the lab copies out of somebody's working copy and must never
    # be able to write into it.
    assert mounts[0].endswith(":ro")
    assert (
        api["environment"]["HARNESS_LAB_HOST_LOCAL_SOURCES_ROOT"]
        == "${HARNESS_LAB_LOCAL_SOURCES_DIR:-${PWD}/..}"
    )


def test_the_shared_root_is_never_a_home_directory(services):
    """Docker Desktop injects filesystem events for every shared path.

    A home directory carries enough of them — ~/Library, caches, synced folders
    — to time that service out and take the engine down with it
    (`service fs failed: injecting event blocked for 60s`), which is exactly
    what happened when this defaulted to $HOME. The default has to stay a
    working set.
    """
    api = services["api"]
    shared = [volume for volume in api["volumes"] if "/host-sources" in volume]

    assert not any("HOME" in volume for volume in shared)
    assert "HOME" not in api["environment"]["HARNESS_LAB_HOST_LOCAL_SOURCES_ROOT"]


def test_only_the_api_can_see_the_operators_own_folders(services):
    """The worker launches containers; a browsable home there widens the blast
    radius for nothing — the copy is made by the API, in its own mount."""
    holders = [
        name
        for name, service in services.items()
        if any("/host-sources" in volume for volume in service.get("volumes", []))
    ]

    assert holders == ["api"]
