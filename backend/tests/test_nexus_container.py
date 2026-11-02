#!/usr/bin/env python3
"""Test Nexus container startup using testcontainers with custom wait logic."""

from testcontainers.core.container import DockerContainer
import tempfile
import sys
import time

def test_nexus_startup():
    print("Starting Nexus container...")
    container = DockerContainer("sonatype/nexus3:latest")
    container.with_exposed_ports(8081, 8082, 8083)
    container.with_env("INSTALL4J_ADD_VM_PARAMS", "-Xms2g -Xms2g -XX:MaxDirectMemorySize=3g")
    container.with_volume_mapping(tempfile.mkdtemp(), "/nexus-data", "rw")
    container.start()
    print("Container started")

    # Wait for Nexus to start (can take 15-20 minutes)
    max_wait_seconds = 1800  # 30 minutes
    check_interval = 15
    start_time = time.time()
    
    print(f"Waiting for Nexus to start (timeout: {max_wait_seconds}s)...")
    while time.time() - start_time < max_wait_seconds:
        logs = container.get_wrapped_container().logs().decode("utf-8", errors="ignore")
        if "Started Sonatype Nexus" in logs:
            print("Nexus started!")
            break
        elapsed = int(time.time() - start_time)
        if elapsed % 60 == 0:
            print(f"  Waiting... ({elapsed}s elapsed)")
        time.sleep(15)
    else:
        print(f"Timeout waiting for Nexus to start after {max_wait_seconds}s")
        return False

    print("Nexus started!")
    ui_port = container.get_exposed_port(8081)
    docker_port = container.get_exposed_port(8082)
    print(f"UI port: {container.get_exposed_port(8081)}, Docker port: {container.get_exposed_port(8082)}")

    container.stop()
    print("Test passed!")
    return True

if __name__ == "__main__":
    try:
        test_nexus_startup()
        sys.exit(0)
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)