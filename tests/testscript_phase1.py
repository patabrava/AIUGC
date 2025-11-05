#!/usr/bin/env python3
"""
FLOW-FORGE Testscript: Phase 1 - Batch Management
Objective: Verify batch CRUD operations, state machine, and dashboard UI
Prerequisites: Phase 0 passing, dev server running
Per Constitution § VIII: Whole-App Testscripts
"""

import sys
import httpx
import json
from datetime import datetime


class TestScriptPhase1:
    """Phase 1 testscript runner."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.Client(timeout=10.0)
        self.passed = 0
        self.failed = 0
        self.artifacts = []
        self.batch_id = None
    
    def log(self, message: str, level: str = "INFO"):
        """Log test message with timestamp."""
        timestamp = datetime.utcnow().isoformat()
        print(f"[{timestamp}] [{level}] {message}")
    
    def checkpoint(self, name: str, passed: bool, details: dict = None):
        """Record checkpoint result."""
        status = "✅ PASS" if passed else "❌ FAIL"
        self.log(f"Checkpoint: {name} - {status}", "CHECK")
        
        if passed:
            self.passed += 1
        else:
            self.failed += 1
        
        if details:
            self.log(f"Details: {json.dumps(details, indent=2)}", "DEBUG")
        
        self.artifacts.append({
            "checkpoint": name,
            "passed": passed,
            "details": details,
            "timestamp": datetime.utcnow().isoformat()
        })
    
    def test_create_batch(self):
        """Test 1: Create a new batch."""
        self.log("Test 1: Create Batch")
        
        try:
            payload = {
                "brand": "Test Brand",
                "post_type_counts": {
                    "value": 3,
                    "lifestyle": 4,
                    "product": 3
                }
            }
            
            response = self.client.post(f"{self.base_url}/batches", json=payload)
            
            if response.status_code != 201:
                self.checkpoint(
                    "Create batch status code",
                    False,
                    {"expected": 201, "actual": response.status_code, "response": response.text}
                )
                return
            
            data = response.json()
            
            # Verify response structure
            if not data.get("ok"):
                self.checkpoint("Create batch response ok", False, {"response": data})
                return
            
            batch = data.get("data", {})
            self.batch_id = batch.get("id")
            
            # Verify initial state
            if batch.get("state") != "S1_SETUP":
                self.checkpoint(
                    "Batch initial state",
                    False,
                    {"expected": "S1_SETUP", "actual": batch.get("state")}
                )
                return
            
            self.checkpoint(
                "Create batch",
                True,
                {
                    "batch_id": self.batch_id,
                    "brand": batch.get("brand"),
                    "state": batch.get("state")
                }
            )
            
        except Exception as e:
            self.checkpoint("Create batch", False, {"error": str(e)})
    
    def test_get_batch(self):
        """Test 2: Get batch by ID."""
        self.log("Test 2: Get Batch by ID")
        
        if not self.batch_id:
            self.checkpoint("Get batch", False, {"error": "No batch_id from previous test"})
            return
        
        try:
            response = self.client.get(f"{self.base_url}/batches/{self.batch_id}")
            
            if response.status_code != 200:
                self.checkpoint(
                    "Get batch status code",
                    False,
                    {"expected": 200, "actual": response.status_code}
                )
                return
            
            data = response.json()
            batch = data.get("data", {})
            
            # Verify batch ID matches
            if batch.get("id") != self.batch_id:
                self.checkpoint(
                    "Get batch ID match",
                    False,
                    {"expected": self.batch_id, "actual": batch.get("id")}
                )
                return
            
            self.checkpoint(
                "Get batch",
                True,
                {
                    "batch_id": batch.get("id"),
                    "state": batch.get("state"),
                    "posts_count": batch.get("posts_count")
                }
            )
            
        except Exception as e:
            self.checkpoint("Get batch", False, {"error": str(e)})
    
    def test_list_batches(self):
        """Test 3: List batches."""
        self.log("Test 3: List Batches")
        
        try:
            response = self.client.get(f"{self.base_url}/batches")
            
            if response.status_code != 200:
                self.checkpoint(
                    "List batches status code",
                    False,
                    {"expected": 200, "actual": response.status_code}
                )
                return
            
            data = response.json()
            batches_data = data.get("data", {})
            batches = batches_data.get("batches", [])
            total = batches_data.get("total", 0)
            
            # Verify our batch is in the list
            batch_ids = [b.get("id") for b in batches]
            found = self.batch_id in batch_ids if self.batch_id else True
            
            self.checkpoint(
                "List batches",
                found,
                {
                    "total": total,
                    "batches_count": len(batches),
                    "test_batch_found": found
                }
            )
            
        except Exception as e:
            self.checkpoint("List batches", False, {"error": str(e)})
    
    def test_state_validation(self):
        """Test 4: State transition validation."""
        self.log("Test 4: State Transition Validation")
        
        if not self.batch_id:
            self.checkpoint("State validation", False, {"error": "No batch_id"})
            return
        
        try:
            # Try invalid transition (S1_SETUP -> S4_SCRIPTED, skipping S2_SEEDED)
            payload = {"target_state": "S4_SCRIPTED"}
            response = self.client.put(
                f"{self.base_url}/batches/{self.batch_id}/state",
                json=payload
            )
            
            # Should fail with 409 (state transition error)
            if response.status_code == 409:
                self.checkpoint(
                    "Invalid state transition rejected",
                    True,
                    {"status_code": response.status_code}
                )
            else:
                self.checkpoint(
                    "Invalid state transition rejected",
                    False,
                    {
                        "expected_status": 409,
                        "actual_status": response.status_code,
                        "response": response.text
                    }
                )
            
        except Exception as e:
            self.checkpoint("State validation", False, {"error": str(e)})
    
    def test_archive_batch(self):
        """Test 5: Archive batch."""
        self.log("Test 5: Archive Batch")
        
        if not self.batch_id:
            self.checkpoint("Archive batch", False, {"error": "No batch_id"})
            return
        
        try:
            payload = {"archived": True}
            response = self.client.put(
                f"{self.base_url}/batches/{self.batch_id}/archive",
                json=payload
            )
            
            if response.status_code != 200:
                self.checkpoint(
                    "Archive batch status code",
                    False,
                    {"expected": 200, "actual": response.status_code}
                )
                return
            
            data = response.json()
            batch = data.get("data", {})
            
            # Verify archived status
            if batch.get("archived") != True:
                self.checkpoint(
                    "Batch archived status",
                    False,
                    {"expected": True, "actual": batch.get("archived")}
                )
                return
            
            self.checkpoint(
                "Archive batch",
                True,
                {"batch_id": self.batch_id, "archived": True}
            )
            
        except Exception as e:
            self.checkpoint("Archive batch", False, {"error": str(e)})
    
    def run(self):
        """Run all tests."""
        self.log("=" * 60)
        self.log("FLOW-FORGE Testscript: Phase 1 - Batch Management")
        self.log("=" * 60)
        self.log(f"Base URL: {self.base_url}")
        self.log("")
        
        # Re-run Phase 0 tests (regression guard)
        self.log("Running Phase 0 regression tests...")
        import subprocess
        result = subprocess.run(
            ["python3", "tests/testscript_phase0.py", self.base_url],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            self.log("❌ Phase 0 regression tests failed!", "ERROR")
            self.log(result.stdout, "ERROR")
            return 1
        
        self.log("✅ Phase 0 regression tests passed", "SUCCESS")
        self.log("")
        
        # Run Phase 1 tests
        self.test_create_batch()
        self.test_get_batch()
        self.test_list_batches()
        self.test_state_validation()
        self.test_archive_batch()
        
        # Summary
        self.log("")
        self.log("=" * 60)
        self.log("SUMMARY")
        self.log("=" * 60)
        self.log(f"Passed: {self.passed}")
        self.log(f"Failed: {self.failed}")
        self.log(f"Total:  {self.passed + self.failed}")
        
        if self.failed == 0:
            self.log("✅ ALL TESTS PASSED", "SUCCESS")
            return 0
        else:
            self.log(f"❌ {self.failed} TEST(S) FAILED", "ERROR")
            return 1


if __name__ == "__main__":
    # Allow custom base URL
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    
    runner = TestScriptPhase1(base_url)
    exit_code = runner.run()
    
    sys.exit(exit_code)
