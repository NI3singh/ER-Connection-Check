# Entity Resolution Module - Complete Testing Guide

## 🎯 TESTING OVERVIEW

**Purpose**: Verify all features work correctly with empty database  
**Time**: 10-15 minutes  
**Prerequisites**: API and Worker running

---

## TEST 1: HEALTH CHECK ✅

**Purpose**: Verify system is operational

```bash
curl http://localhost:8001/api/v1/health
```

**Expected Output**:
```json
{
  "status": "healthy",
  "timestamp": "2025-12-11T...",
  "version": "2.0.0",
  "worker_status": "IDLE",
  "last_analysis": null,
  "statistics": {
    "total_metadata_records": 0,
    "active_entity_links": 0,
    "active_clusters": 0,
    "high_risk_clusters": 0,
    "last_processed_id": 0
  }
}
```

**✅ Pass Criteria**: `status: "healthy"`, all counts = 0

---

## TEST 2: INGEST SINGLE USER (No Links)

**Purpose**: Test basic metadata ingestion

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

**Expected Output**:
```json
{
  "status": "success",
  "message": "Metadata created and queued for analysis"
}
```

**Wait 60 seconds for worker cycle**

```bash
# Check if user is flagged
curl http://localhost:8001/api/v1/user/alice/risk
```

**Expected Output**:
```json
{
  "user_id": "alice",
  "is_flagged": false,
  "risk_score": 0.0,
  "risk_level": "SAFE",
  "cluster_id": null,
  "cluster_size": 0,
  "reason": "No entity links detected"
}
```

**✅ Pass Criteria**: Single user = NOT flagged (no links possible)

---

## TEST 3: UPSERT LOGIC (Same User, Same Data)

**Purpose**: Verify duplicate prevention

```bash
# Send SAME data again (should UPDATE, not INSERT)
curl -X POST http://localhost:8001/api/v1/ingest/user-metadata \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "alice",
    "device_hash": "device_alice_phone",
    "ip_address": "103.45.67.1",
    "canvas_hash": "canvas_alice_123"
  }'
```

**Expected Output**:
```json
{
  "status": "success",
  "message": "Metadata updated and queued for analysis"
}
```

**Verify in Database**:
```bash
psql -U postgres -d aml_db -c "SELECT user_id, occurrence_count FROM user_metadata WHERE user_id='alice';"
```

**Expected**:
```
 user_id | occurrence_count 
---------+------------------
 alice   |                2
```

**✅ Pass Criteria**: Only 1 row, occurrence_count = 2

---

## TEST 4: DEVICE SHARING (HIGH CONFIDENCE LINK)

**Purpose**: Detect two users sharing same device (fraud indicator)

```bash
# User Bob uses SAME device as Alice
curl -X POST http://localhost:8001/api/v1/ingest/user-metadata \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "bob",
    "device_hash": "device_alice_phone",
    "ip_address": "103.45.67.2",
    "canvas_hash": "canvas_bob_456"
  }'
```

**Wait 60 seconds for worker to detect link**

```bash
# Check Bob's risk
curl http://localhost:8001/api/v1/user/bob/risk
```

**Expected Output**:
```json
{
  "user_id": "bob",
  "is_flagged": true,
  "risk_score": 0.85,
  "risk_level": "CRITICAL",
  "cluster_id": "Ring-alice",
  "cluster_size": 2,
  "density_score": 1.0,
  "confidence_score": 0.95,
  "reason": "User linked to CRITICAL risk cluster: Ring-alice"
}
```

**Also check Alice**:
```bash
curl http://localhost:8001/api/v1/user/alice/risk
```

**Expected**: Alice also flagged in same cluster

**✅ Pass Criteria**: 
- Both flagged: TRUE
- Same cluster_id
- risk_score >= 0.85
- Shared device = HIGH confidence (0.95)

---

## TEST 5: FAMILY WIFI (LOW CONFIDENCE - NOT LINKED)

**Purpose**: Verify IP-only sharing doesn't create false positives

```bash
# User Charlie shares ONLY IP with Alice (family WiFi)
curl -X POST http://localhost:8001/api/v1/ingest/user-metadata \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "charlie",
    "device_hash": "device_charlie_laptop",
    "ip_address": "103.45.67.1",
    "canvas_hash": "canvas_charlie_789"
  }'
```

**Wait 60 seconds**

```bash
curl http://localhost:8001/api/v1/user/charlie/risk
```

**Expected Output**:
```json
{
  "user_id": "charlie",
  "is_flagged": false,
  "risk_score": 0.0,
  "risk_level": "SAFE",
  "cluster_id": null,
  "cluster_size": 0,
  "reason": "No entity links detected"
}
```

**✅ Pass Criteria**: 
- Charlie NOT linked to Alice
- IP alone (confidence 0.30) < threshold (0.70)
- No false positive

---

## TEST 6: MULTI-SIGNAL LINK (DEVICE + IP)

**Purpose**: Test confidence scoring with multiple shared signals

```bash
# User Dave shares DEVICE + IP with Alice
curl -X POST http://localhost:8001/api/v1/ingest/user-metadata \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "dave",
    "device_hash": "device_alice_phone",
    "ip_address": "103.45.67.1",
    "canvas_hash": "canvas_dave_999"
  }'
```

**Wait 60 seconds**

```bash
curl http://localhost:8001/api/v1/user/dave/risk
```

**Expected Output**:
```json
{
  "user_id": "dave",
  "is_flagged": true,
  "risk_score": 0.92,
  "risk_level": "CRITICAL",
  "cluster_id": "Ring-alice",
  "cluster_size": 3,
  "reason": "User linked to CRITICAL risk cluster"
}
```

**✅ Pass Criteria**: 
- Dave linked (DEVICE + IP confidence > 0.70)
- Cluster now has 3 members (alice, bob, dave)
- Charlie still NOT in cluster

---

## TEST 7: CLUSTER DETAILS

**Purpose**: View detailed cluster information

```bash
curl http://localhost:8001/api/v1/cluster/Ring-alice
```

**Expected Output**:
```json
{
  "cluster_id": "Ring-alice",
  "size": 3,
  "risk_score": 0.92,
  "density_score": 1.0,
  "confidence_score": 0.95,
  "formation_date": "2025-12-11T...",
  "review_status": "PENDING",
  "members": [
    {"user_id": "alice", "risk_score": 0.92},
    {"user_id": "bob", "risk_score": 0.90},
    {"user_id": "dave", "risk_score": 0.88}
  ],
  "connections": [
    {
      "user_a": "alice",
      "user_b": "bob",
      "confidence": 0.95,
      "link_type": "PRIMARY",
      "shared_signals": "[\"DEVICE\"]"
    },
    {
      "user_a": "alice",
      "user_b": "dave",
      "confidence": 0.76,
      "link_type": "SECONDARY",
      "shared_signals": "[\"DEVICE\", \"IP\"]"
    }
  ]
}
```

**✅ Pass Criteria**: 
- Shows all 3 members
- Shows connections between them
- Confidence scores visible

---

## TEST 8: COMPLIANCE DASHBOARD

**Purpose**: View all flagged clusters

```bash
curl "http://localhost:8001/api/v1/compliance/clusters?min_risk=0.7&status=PENDING"
```

**Expected Output**:
```json
{
  "count": 1,
  "filters": {
    "min_risk": 0.7,
    "status": "PENDING"
  },
  "clusters": [
    {
      "cluster_id": "Ring-alice",
      "risk_score": 0.92,
      "size": 3,
      "formation_date": "2025-12-11T..."
    }
  ]
}
```

**✅ Pass Criteria**: Shows 1 cluster pending review

---

## TEST 9: MANUAL REVIEW (FALSE POSITIVE)

**Purpose**: Test compliance officer workflow

```bash
# Mark cluster as FALSE_POSITIVE
curl -X POST "http://localhost:8001/api/v1/compliance/review/Ring-alice?decision=FALSE_POSITIVE"
```

**Expected Output**:
```json
{
  "status": "success",
  "cluster_id": "Ring-alice",
  "decision": "FALSE_POSITIVE",
  "message": "Cluster marked as FALSE_POSITIVE"
}
```

**Verify**:
```bash
curl http://localhost:8001/api/v1/user/alice/risk
```

**Expected**: Users still flagged BUT review_status = "FALSE_POSITIVE"

**✅ Pass Criteria**: Manual review recorded

---

## TEST 10: PUBLIC WIFI (SIGNAL STRENGTH)

**Purpose**: Test dynamic signal weighting

```bash
# Create 15 users on SAME IP (simulate public WiFi)
for i in {1..15}; do
  curl -X POST http://localhost:8001/api/v1/ingest/user-metadata \
    -H "Content-Type: application/json" \
    -d "{
      \"user_id\": \"public_user_$i\",
      \"device_hash\": \"device_unique_$i\",
      \"ip_address\": \"103.45.67.99\",
      \"canvas_hash\": \"canvas_unique_$i\"
    }"
done
```

**Wait 60 seconds**

**Check signal strength**:
```bash
psql -U postgres -d aml_db -c "SELECT signal_type, signal_value, user_count, confidence_weight FROM signal_strengths WHERE signal_value='103.45.67.99';"
```

**Expected**:
```
 signal_type |  signal_value  | user_count | confidence_weight 
-------------+----------------+------------+-------------------
 IP          | 103.45.67.99   |         15 |              0.15
```

**Check if users linked**:
```bash
curl http://localhost:8001/api/v1/user/public_user_1/risk
```

**Expected**: NOT flagged (IP confidence too low)

**✅ Pass Criteria**: 
- IP shared by 15 users
- Confidence weight dropped to 0.15
- Users NOT linked (need device/canvas match)

---

## TEST 11: INCREMENTAL PROCESSING

**Purpose**: Verify worker only processes NEW data

```bash
# Check analysis state
psql -U postgres -d aml_db -c "SELECT last_processed_metadata_id, worker_status FROM analysis_state;"
```

**Expected**:
```
 last_processed_metadata_id | worker_status 
----------------------------+---------------
                         19 | IDLE
```

**Add new user**:
```bash
curl -X POST http://localhost:8001/api/v1/ingest/user-metadata \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "new_user",
    "device_hash": "device_new",
    "ip_address": "103.45.67.100",
    "canvas_hash": "canvas_new"
  }'
```

**Wait 60 seconds, then check**:
```bash
psql -U postgres -d aml_db -c "SELECT last_processed_metadata_id FROM analysis_state;"
```

**Expected**:
```
 last_processed_metadata_id 
----------------------------
                         20
```

**✅ Pass Criteria**: ID incremented by 1 (only processed 1 new record)

---

## TEST 12: WORKER RESTART RESILIENCE

**Purpose**: Verify worker resumes from last position

```bash
# Stop worker
pkill -f "python worker.py"

# Add metadata while worker is DOWN
curl -X POST http://localhost:8001/api/v1/ingest/user-metadata \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "offline_user",
    "device_hash": "device_offline",
    "ip_address": "103.45.67.200",
    "canvas_hash": "canvas_offline"
  }'

# Restart worker
python worker.py &

# Wait 60 seconds

# Check processing
psql -U postgres -d aml_db -c "SELECT last_processed_metadata_id FROM analysis_state;"
```

**Expected**: ID incremented (caught up with missed data)

**✅ Pass Criteria**: Worker processes backlog after restart

---

## 📊 FINAL VERIFICATION

**Check all tables**:
```bash
psql -U postgres -d aml_db << EOF
SELECT 'user_metadata' as table, COUNT(*) as count FROM user_metadata
UNION ALL
SELECT 'signal_strengths', COUNT(*) FROM signal_strengths
UNION ALL
SELECT 'entity_links', COUNT(*) FROM entity_links WHERE is_active=true
UNION ALL
SELECT 'smurf_clusters', COUNT(*) FROM smurf_clusters WHERE is_active=true
UNION ALL
SELECT 'analysis_state', COUNT(*) FROM analysis_state;
EOF
```

**Expected**:
```
      table       | count 
------------------+-------
 user_metadata    |    21
 signal_strengths |     7
 entity_links     |     3
 smurf_clusters   |     3
 analysis_state   |     1
```

---

## ✅ TESTING CHECKLIST

- [ ] Test 1: Health check passes
- [ ] Test 2: Single user NOT flagged
- [ ] Test 3: UPSERT prevents duplicates
- [ ] Test 4: Device sharing = LINKED (high confidence)
- [ ] Test 5: IP-only sharing = NOT LINKED (low confidence)
- [ ] Test 6: Multi-signal = LINKED (weighted confidence)
- [ ] Test 7: Cluster details show connections
- [ ] Test 8: Compliance dashboard works
- [ ] Test 9: Manual review workflow
- [ ] Test 10: Public WiFi detection (signal strength)
- [ ] Test 11: Incremental processing
- [ ] Test 12: Worker restart resilience

---

## 🐛 TROUBLESHOOTING

**Issue**: Users not getting linked after 60s  
**Fix**: Check worker logs: `tail -f worker.log`

**Issue**: All users flagged (false positives)  
**Fix**: Adjust `MIN_LINK_CONFIDENCE` in `graph_engine.py`

**Issue**: Worker status = ERROR  
**Fix**: Check health endpoint, restart worker

**Issue**: Database errors  
**Fix**: Run `python database.py` again

---

## 📈 EXPECTED RESULTS SUMMARY

| Test | Input | Expected Result |
|------|-------|----------------|
| Single user | 1 user | NOT flagged |
| Device sharing | 2 users, same device | FLAGGED (0.95 conf) |
| Family WiFi | 2 users, same IP | NOT flagged (0.30 conf) |
| Multi-signal | Device + IP | FLAGGED (0.76 conf) |
| Public WiFi | 15 users, same IP | NOT linked (0.15 conf) |
| Manual review | Mark false positive | Status updated |
| Worker restart | Add data while down | Catches up |

**All tests passing = System working correctly! ✅**