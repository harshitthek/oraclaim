<div align="center">

# 🚀 Universal OCI Smart Auto-Claimer

**A high-performance, asynchronous multi-worker engine designed to claim Always-Free Ampere A1 ARM and AMD compute instances on Oracle Cloud Infrastructure (OCI).**

[![CI](https://github.com/harshitthek/oci-arm-smart-claimer/actions/workflows/ci.yml/badge.svg)](https://github.com/harshitthek/oci-arm-smart-claimer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9%2B-brightgreen.svg)](https://www.python.org/)
[![Platform: Linux / Windows / Docker](https://img.shields.io/badge/Platform-Linux%20%7C%20Windows%20%7C%20Docker-orange.svg)]()

</div>

---

## ⚡ Key Highlights

Unlike basic single-threaded pollers that get throttled or crash, this engine uses a distributed multi-worker architecture with real-time inter-worker synchronization:

* 🎯 **Universal Shape & OS Support:** Fully configurable for **any instance shape** (`VM.Standard.A1.Flex` ARM up to 4 OCPUs/24 GB RAM, `VM.Standard.E2.1.Micro` AMD, etc.) and **any OS** (Canonical Ubuntu, Oracle Linux, Debian, etc.).
* ⏱️ **Phase-Locked Multi-Worker Interleaving:** Configurable concurrent asynchronous workers poll with dynamic phase offsets to achieve a steady, balanced cadence without collision or redundant API calls.
* 🧠 **AIMD Rate-Limit Protection (Additive Increase, Multiplicative Decrease):** Dynamically detects HTTP 429 (`TooManyRequests`) and broadcasts instant cooldowns across all workers, keeping your account 100% compliant and ban-free.
* 🌐 **Wildcard Placement (`ANY_FD`):** Alternates between specific server racks (`FAULT-DOMAIN-1`, `2`, `3`) and **Wildcard Placement** (commanding Oracle to search all physical racks across the entire datacenter at once).
* 🔥 **Top-of-Hour Surge Mode:** Automatically accelerates polling frequency during **:00, :15, :30, and :45** when expired trial accounts and idle tenancies are terminated by Oracle's batch cleanups.
* ⚡ **HTTP Connection Pooling & Keep-Alive:** Reuses TLS/TCP sockets to drop API latency to **~0.6s–0.8s**, beating competing claimers to newly released slots.
* 🛡️ **Self-Healing Linux Systemd Daemon:** Runs 24/7 as a managed system service with auto-recovery (`Restart=always`) and bounded memory guards.

---

## 🏗️ Architecture

```
                                  ┌────────────────────────────────────────┐
                                  │      Central Claim Coordinator         │
                                  │  - Phase-Lock Timing Master            │
                                  │  - Shared 429 Threat Broadcast         │
                                  │  - Dynamic FD Heatmap Router           │
                                  │  - Atomic Emergency Stop Mutex         │
                                  └───────────────────┬────────────────────┘
                                                      │
                       ┌──────────────────────────────┴──────────────────────────────┐
                       │                                                             │
                       ▼                                                             ▼
            ┌─────────────────────┐                                       ┌─────────────────────┐
            │    Worker-Alpha     │                                       │     Worker-Beta     │
            │                     │◄──────── Inter-Worker Sync ──────────►│                     │
            │ Phase: 0.0s offset  │               Heartbeat               │ Phase: 14.0s offset │
            │ Target: ANY_FD, FD-1│                                       │ Target: FD-2, FD-3  │
            └──────────┬──────────┘                                       └──────────┬──────────┘
                       │                                                             │
                       └──────────────────────────────┬──────────────────────────────┘
                                                      │
                                                      ▼
                                       ┌─────────────────────────────┐
                                       │   Oracle Cloud Compute API  │
                                       │    https://iaas.region...   │
                                       └─────────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/oci-arm-smart-claimer.git
cd oci-arm-smart-claimer
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Credentials
Copy the example config template and insert your Oracle Cloud OCIDs:
```bash
cp config.example.ini config.ini
```

Edit `config.ini` with your details:
```ini
[DEFAULT]
user=ocid1.user.oc1..aaaaaaaa...
fingerprint=00:11:22:33:44:55:66:77:88:99:aa:bb:cc:dd:ee:ff
key_file=/path/to/your/oci_api_key.pem
tenancy=ocid1.tenancy.oc1..aaaaaaaa...
region=ap-mumbai-1
```

Place your public SSH key (`id_rsa.pub` or `ssh-key.pub`) in the project root.

---

## 💻 CLI Options & Flexibility

```bash
# Standard 1 Core / 6 GB Ubuntu ARM Node
python -m src.cli

# Max Always-Free ARM Specs (4 OCPUs / 24 GB RAM / 100 GB Boot Volume)
python -m src.cli --ocpus 4.0 --memory 24.0 --boot-volume-gbs 100 --name "High-Memory-Node"

# Oracle Linux 9 ARM Instance
python -m src.cli --os "Oracle Linux" --os-version "9" --ocpus 2.0 --memory 12.0

# Free-Tier AMD Micro Instance (x86_64)
python -m src.cli --shape "VM.Standard.E2.1.Micro" --os "Canonical Ubuntu" --name "AMD-Micro-Node"

# High-Concurrency Polling (3 Workers, 20s base cadence)
python -m src.cli --workers 3 --cadence 20.0

# Validate credentials and resource discovery without launching
python -m src.cli --dry-run
```

---

## 🐧 24/7 Linux Systemd Deployment (Recommended)

To run the claimer continuously in the background on any Oracle Linux, Ubuntu, or Debian instance:

```bash
# 1. Run the 1-click installer
chmod +x scripts/install_systemd.sh
./scripts/install_systemd.sh

# 2. Check live status and logs
chmod +x scripts/status.sh
./scripts/status.sh

# 3. Stream real-time logs
tail -f claim_arm.log
```

---

## 🐳 Docker Deployment

```bash
# Run with Docker Compose
docker compose up -d

# View live container logs
docker compose logs -f
```

---

## 🧪 Running Tests

```bash
# Install test dependencies
pip install -r requirements-dev.txt

# Run pytest suite with coverage
pytest -v --cov=src
```

---

## 🔒 Security & Privacy

* This repository is pre-configured with a hardened `.gitignore` that prevents `.key`, `.pem`, `config.ini`, and credentials from ever being tracked or committed.
* Always keep your private API signing keys and tenancy secrets confidential.

---

## 📄 License

Distributed under the **MIT License**. See `LICENSE` for more information.
