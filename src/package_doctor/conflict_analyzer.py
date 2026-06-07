"""Parse pip freeze + pip check output and detect conflicts."""
import re
from typing import Optional

from packaging.requirements import Requirement
from packaging.version import Version, InvalidVersion

from package_doctor.models import ConflictEntry


def parse_pip_freeze(pip_freeze: str) -> dict[str, str]:
    """Return {package_name_lower: version} from pip freeze output."""
    installed: dict[str, str] = {}
    for line in pip_freeze.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if "==" in line:
            name, _, version = line.partition("==")
            installed[name.strip().lower()] = version.strip()
    return installed


def parse_pip_check(pip_check: str) -> list[ConflictEntry]:
    """
    Parse `pip check` output into ConflictEntry list.

    Handles single and multi-part specs:
      sphinx 4.3.0 requires docutils<0.18,>=0.14, but you have docutils 0.18.1 which is incompatible.
      package-a 1.0 has requirement package-b>=2.0, but you have package-b 1.5.
    """
    conflicts = []
    # Capture everything between "requires/has requirement" and ", but you have"
    # .+? is non-greedy so it stops at the FIRST ", but you have" (space after comma)
    pattern = re.compile(
        r"(?P<requirer>\S+)\s+\S+\s+(?:requires|has requirement)\s+"
        r"(?P<requirement>.+?),\s+but you have\s+(?P<dep2>\S+)\s+(?P<ver>\S+)",
        re.IGNORECASE,
    )
    _dep_name = re.compile(r"^([a-zA-Z0-9_\-\.]+)")
    for line in pip_check.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            requirement = m.group("requirement")
            installed_ver = m.group("ver").rstrip(".")
            required_by = m.group("requirer").lower()
            dn = _dep_name.match(requirement)
            dep_name = dn.group(1).lower() if dn else m.group("dep2").lower()
            spec = requirement[len(dn.group(1)):] if dn else ""
            conflicts.append(
                ConflictEntry(
                    package=dep_name,
                    required_spec=spec,
                    installed_version=installed_ver,
                    required_by=required_by,
                    severity="error",
                )
            )
    return conflicts


def build_fix_commands(
    conflicts: list[ConflictEntry], package_manager: str = "pip"
) -> list[str]:
    """Generate ranked fix commands for a list of conflicts."""
    cmds = []
    seen = set()
    for c in conflicts:
        spec = f"{c.package}{c.required_spec}" if c.required_spec else c.package
        if package_manager == "uv":
            cmd = f'uv add "{spec}"'
        elif package_manager == "conda":
            cmd = f'conda install "{spec}"'
        else:
            cmd = f'pip install "{spec}"'
        if cmd not in seen:
            cmds.append(cmd)
            seen.add(cmd)
    return cmds


def check_pre_install_conflicts(
    package_name: str,
    pip_freeze: str,
    package_manager: str = "pip",
) -> tuple[bool, list[ConflictEntry], list[str]]:
    """
    Check whether installing package_name would conflict with current environment.
    Returns (would_conflict, conflicts, resolution_commands).

    Note: we can only detect version-string conflicts for packages already installed.
    True resolver-level detection requires pip dry-run; this is a fast heuristic.
    """
    installed = parse_pip_freeze(pip_freeze)
    name_lower = package_name.lower()

    # Basic: if already installed at a different version, flag it
    conflicts = []
    if name_lower in installed:
        conflicts.append(
            ConflictEntry(
                package=name_lower,
                required_spec="(latest)",
                installed_version=installed[name_lower],
                required_by="(new install)",
                severity="warning",
            )
        )

    resolution_commands = build_fix_commands(conflicts, package_manager)
    return bool(conflicts), conflicts, resolution_commands
