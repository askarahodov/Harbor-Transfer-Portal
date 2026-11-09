"""Bounded skopeo inspect projection shared by Harbor and Nexus transports.

Full `skopeo inspect` JSON includes RepoTags (potentially thousands of
entries) and layer/config metadata. The services need only the manifest
digest and the selected OS/architecture; --no-tags also skips the
potentially costly registry-wide tag enumeration.
"""

# Go text/template builtins (printf %q) JSON-escape each individual field.
# Use only fields that skopeo inspect exposes for Docker and OCI transports.
# The template and flags are constants; no registry/user data is interpolated
# into CLI arguments by Python.
COMPACT_INSPECT_FORMAT = (
    '{"Digest":{{printf "%q" .Digest}},'
    '"Architecture":{{printf "%q" .Architecture}},'
    '"Os":{{printf "%q" .Os}}}'
)
COMPACT_INSPECT_FLAGS = ("--no-tags", "--format", COMPACT_INSPECT_FORMAT)


def is_output_truncated(stdout: str) -> bool:
    """Recognize the runner's synthetic overflow marker (never accept partial JSON)."""
    return stdout.endswith("\n[output truncated]")
