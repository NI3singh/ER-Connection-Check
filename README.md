# AML Entity Resolution Module

## Overview
A powerful system for detecting linked users and potential fraud rings ("smurf clusters") by analyzing shared metadata signals such as device hashes, IP addresses, and canvas hashes. It uses a graph-based approach to link entities, calculate risk scores, and help compliance teams identify suspicious activity.

## Features
*   **Metadata Ingestion:** Ingest user metadata (device, IP, canvas) via API.
*   **Entity Linking:** Automatically links users based on shared signals with configurable confidence thresholds.
*   **Risk Scoring:** Calculates risk scores for users and clusters based on connection density and signal strength.
*   **Cluster Analysis:** Identifies clusters of linked users ("smurf clusters").
*   **False Positive Reduction:** Distinguishes between high-confidence links (shared device) and low-confidence links (shared IP/public WiFi).
*   **Dynamic Signal Weighting:** Automatically adjusts confidence based on signal ubiquity (e.g., widely shared IPs like public WiFi get lower weight).
*   **Compliance Dashboard:** API endpoints for reviewing flagged clusters and managing false positives.
*   **Incremental Processing:** Efficient background worker processes only new data for optimal performance.

## Prerequisites
*   Python 3.x
*   PostgreSQL

## Installation

### 1. Automatic Setup (Recommended)
```bash
chmod +x setup.sh
./setup.sh
```

### 2. Manual Setup
1.  **Initialize Database:**
    ```bash
    cd app
    python database.py
    ```

2.  **Start API:**
    ```bash
    uvicorn main:app --host 0.0.0.0 --port 8001
    ```

3.  **Start Worker:**
    ```bash
    python worker.py
    ```

### 3. Production Setup (Systemd)
See `INSTALL.txt` for details on setting up the `aml-worker` service.

## Usage

### Check Health
```bash
curl http://localhost:8001/api/v1/health
```

### Ingest User Metadata
```bash
curl -X POST http://localhost:8001/api/v1/ingest/user-metadata \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "alice",
    "device_hash": "device_alice_phone",
    "ip_address": "103.45.67.1",
    "canvas_hash": "canvas_alice_123"
  }'
```

### Check User Risk
```bash
curl http://localhost:8001/api/v1/user/alice/risk
```

### View Cluster Details
```bash
curl http://localhost:8001/api/v1/cluster/{cluster_id}
```

## Testing
A comprehensive testing guide is available in [complete_testing.md](complete_testing.md). It covers scenarios like:
*   Device sharing (High confidence link)
*   Family WiFi vs Public WiFi (Signal strength analysis)
*   Multi-signal linking
*   Manual review workflow

## Architecture
The system consists of three main components:
1.  **API (`app/main.py`):** Handles metadata ingestion and serves compliance data.
2.  **Worker (`app/worker.py`):** Background process that analyzes new data, builds the entity graph, and updates risk scores.
3.  **Database:** PostgreSQL database storing user metadata, entity links, and cluster information.
