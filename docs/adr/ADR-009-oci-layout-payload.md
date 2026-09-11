# ADR-009: Use OCI image-layout directories inside offline bundles

## Status
Accepted for bundle protocol v1.

## Context
The portal must move container images, including multi-architecture indexes, across an air gap while preserving verifiable OCI identity. The transport bundle is already a tar.gz archive, so the internal image representation does not need to be another Docker-style tar archive.

## Decision
Use an OCI image-layout directory as the image payload representation. Export/import services will use Skopeo OCI transport with all manifests/platforms preserved where requested.

## Rationale
- OCI layout preserves indexes, manifests, blobs and digest-addressed content directly.
- It is suitable for multi-architecture content when Skopeo copies all manifests.
- Digest verification can be performed against OCI metadata rather than inferring identity from tar filenames.
- The outer `.htp.tar.gz` already provides single-file physical portability.
- Directory payloads make checksum coverage explicit per file and allow strict archive extraction validation.

## Rejected alternative
`docker-archive:`/single-image tar representations are not selected because they are less natural for OCI indexes and multi-arch preservation. `oci-archive:` remains a possible future transport optimization, but v1 chooses the directory form to make structure and verification explicit.

## Consequences
Bundle verification must checksum all OCI-layout files, enforce safe relative paths, and independently inspect the imported target artifact after transfer. Successful Skopeo process exit is not sufficient proof of delivery.
