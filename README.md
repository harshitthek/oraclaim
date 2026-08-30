<div align="center">

# ⚡ OraClaim
### Intelligent, Multi-Worker Auto-Provisioning Engine for Oracle Cloud

*High-performance, ban-resistant automation designed to secure Always-Free Ampere A1 ARM & AMD compute instances the millisecond capacity drops.*

---

[![CI](https://github.com/harshitthek/oraclaim/actions/workflows/ci.yml/badge.svg)](https://github.com/harshitthek/oraclaim/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![OCI SDK](https://img.shields.io/badge/OCI%20SDK-Ready-F80000?logo=oracle&logoColor=white)](https://docs.oracle.com/en-us/iaas/Content/API/Concepts/sdk_challenges.htm)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20Windows%20%7C%20Docker-555555)]()

<br/>

[Key Highlights](#-why-oraclaim) •
[Architecture](#-architecture) •
[Quick Start](#-quick-start) •
[CLI Cheat Sheet](#-cli-usage--examples) •
[24/7 Deployment](#-247-linux-daemon-recommended) •
[Docker](#-docker-quickstart)

</div>

---

## 🌟 Why OraClaim?

Most cloud claimers are basic `while True` loops with static sleep timers that quickly trigger **HTTP 429 rate-limit bans**, fail to adapt to maintenance drops, or get killed during memory spikes.

**OraClaim** solves this with a multi-threaded, phase-locked architecture:

| Feature | Basic Claimer Scripts | ⚡ OraClaim Engine |
| :--- | :---: | :---: |
| **Concurrency Model** | Single-threaded sequential | **Phase-Locked Multi-Worker Interleaving** |
| **Rate-Limit Handling** | Static hard sleep / crashes on 429 | **AIMD Adaptive Threat Broadcast** |
| **Datacenter Coverage** | Single locked fault domain | **Wildcard (`ANY_FD`) + Fault Domain Rotation** |
| **Drop Detection** | Misses top-of-hour release windows | **🔥 Top-of-Hour Multi-Target Surge Bursts** |
| **Network Overhead** | Re-authenticates every cycle (~1.5s) | **Persistent HTTP Connection Pooling (~0.6s)** |
| **24/7 Resilience** | Unmanaged background `nohup` | **Self-Healing Linux Systemd Daemon** |

---

## 🏗️ Architecture

OraClaim operates a centralized coordinator that phase-locks multiple asynchronous workers to maintain an equidistant polling cadence without API collisions.

```
                           ┌────────────────────────────────────────┐
                           │      Central Claim Coordinator         │
                           │  • Phase-Lock Timing Master            │
                           │  • Shared 429 Threat Broadcast         │
                           │  • Wildcard / FD Heatmap Router        │
                           │  • Atomic Mutual Exclusion Lock        │
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

### 1. Clone & Install
```bash
git clone https://github.com/your-username/oraclaim.git
cd oraclaim
pip install -r requirements.txt
```

### 2. Configure Credentials
Copy the example config and add your Oracle Cloud OCIDs:
```bash
cp config.example.ini config.ini
```

```ini
[DEFAULT]
user=ocid1.user.oc1..aaaaaaaa...
fingerprint=00:11:22:33:44:55:66:77:88:99:aa:bb:cc:dd:ee:ff
key_file=/path/to/your/oci_api_key.pem
tenancy=ocid1.tenancy.oc1..aaaaaaaa...
region=ap-mumbai-1
```

> [!NOTE]
> Place your public SSH key (`id_rsa.pub` or `ssh-key.pub`) in the project directory.

---

## 💻 CLI Usage & Examples

OraClaim is fully configurable via CLI arguments or environment variables:

```bash
# 🎯 1. Standard 1 Core / 6 GB Ubuntu ARM Instance
python -m src.cli

# 🚀 2. Maximum Always-Free Specs (4 OCPUs / 24 GB RAM / 100 GB Disk)
python -m src.cli --ocpus 4.0 --memory 24.0 --boot-volume-gbs 100 --name "Primary-Node"

# 🐧 3. Oracle Linux 9 ARM Node
python -m src.cli --os "Oracle Linux" --os-version "9" --ocpus 2.0 --memory 12.0

# 💻 4. Free-Tier AMD Micro (x86_64) Instance
python -m src.cli --shape "VM.Standard.E2.1.Micro" --name "AMD-Micro-Node"

# ⚡ 5. High-Frequency Polling (3 Workers, 18s Cadence)
python -m src.cli --workers 3 --cadence 18.0

# 🔍 6. Validate Setup & Credentials (Dry Run)
python -m src.cli --dry-run
```

---

## 🐧 24/7 Linux Daemon (Recommended)

Install OraClaim as a managed `systemd` service on any Oracle Linux, Ubuntu, or Debian server in one command:

```bash
# 1. Run the automated installer
chmod +x scripts/install_systemd.sh
./scripts/install_systemd.sh

# 2. Check live status & recent heartbeats
chmod +x scripts/status.sh
./scripts/status.sh

# 3. Stream real-time logs
tail -f claim_arm.log
```

> [!TIP]
> The installer automatically disables heavy background DNF/APT timers to ensure the daemon never exceeds **60 MB RAM**, keeping it 100% stable on low-memory 1 GB instances.

---

## 🐳 Docker Quickstart

```bash
# Start background container
docker compose up -d

# View live stream
docker compose logs -f
```

---

## 🧪 Testing

```bash
# Install test dependencies
pip install -r requirements-dev.txt

# Run full test suite with coverage
pytest -v --cov=src
```

---

## 🔒 Security Guarantee

* **Strict `.gitignore`**: Pre-configured to prevent `.key`, `.pem`, `config.ini`, `.env`, and log files from ever being tracked or committed to GitHub.
* **Mutual Exclusion**: Stops all workers instantly once an instance is secured to guarantee zero over-quota charges.

---

## 📄 License

Distributed under the **MIT License**. See `LICENSE` for details.
