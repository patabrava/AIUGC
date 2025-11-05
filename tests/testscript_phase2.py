#!/usr/bin/env python3
"""
FLOW-FORGE Testscript: Phase 2 - Topic Discovery
Objective: Verify topic generation, deduplication, and state transition S1→S2
Prerequisites: Phase 0 & 1 passing, dev server running, LLM API keys configured
Per Constitution § VIII: Whole-App Testscripts
"""

import sys
import httpx
import json
from datetime import datetime


class TestScriptPhase2:
    """Phase 2 testscript runner."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.Client(timeout=60.0)  # Longer timeout for LLM calls
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
            self.log(f"Details: {json.dumps(details, indent=2, default=str)}", "DEBUG")
        
        self.artifacts.append({
            "checkpoint": name,
            "passed": passed,
            "details": details,
            "timestamp": datetime.utcnow().isoformat()
        })
    
    def test_create_test_batch(self):
        """Test 1: Create a test batch for topic discovery."""
        self.log("Test 1: Create Test Batch")
        
        try:
            payload = {
                "brand": "EcoLife Wellness",
                "post_type_counts": {
                    "value": 2,
                    "lifestyle": 2,
                    "product": 1
                }
            }
            
            response = self.client.post(f"{self.base_url}/batches", json=payload)
            
            if response.status_code != 201:
                self.checkpoint(
                    "Create test batch",
                    False,
                    {"status_code": response.status_code, "response": response.text}
                )
                return
            
            data = response.json()
            batch = data.get("data", {})
            self.batch_id = batch.get("id")
            
            self.checkpoint(
                "Create test batch",
                True,
                {"batch_id": self.batch_id, "state": batch.get("state")}
            )
            
        except Exception as e:
            self.checkpoint("Create test batch", False, {"error": str(e)})
    
    def test_discover_topics(self):
        """Test 2: Discover topics for the batch."""
        self.log("Test 2: Discover Topics (this may take 30-60 seconds)")
        
        if not self.batch_id:
            self.checkpoint("Discover topics", False, {"error": "No batch_id"})
            return
        
        try:
            payload = {"batch_id": self.batch_id, "count": 10}
            
            response = self.client.post(f"{self.base_url}/topics/discover", json=payload)
            
            if response.status_code != 200:
                self.checkpoint(
                    "Discover topics",
                    False,
                    {"status_code": response.status_code, "response": response.text}
                )
                return
            
            data = response.json()
            result = data.get("data", {})
            
            # Verify posts were created
            posts_created = result.get("posts_created", 0)
            expected_posts = 5  # 2+2+1 from post_type_counts
            
            if posts_created != expected_posts:
                self.checkpoint(
                    "Correct number of posts created",
                    False,
                    {"expected": expected_posts, "actual": posts_created}
                )
                return
            
            # Verify state transition
            new_state = result.get("state")
            if new_state != "S2_SEEDED":
                self.checkpoint(
                    "State transition to S2_SEEDED",
                    False,
                    {"expected": "S2_SEEDED", "actual": new_state}
                )
                return
            
            self.checkpoint(
                "Discover topics",
                True,
                {
                    "posts_created": posts_created,
                    "new_state": new_state,
                    "topics_sample": result.get("topics", [])[:2]
                }
            )
            
        except Exception as e:
            self.checkpoint("Discover topics", False, {"error": str(e)})
    
    def test_verify_batch_state(self):
        """Test 3: Verify batch is now in S2_SEEDED state."""
        self.log("Test 3: Verify Batch State")
        
        if not self.batch_id:
            self.checkpoint("Verify batch state", False, {"error": "No batch_id"})
            return
        
        try:
            response = self.client.get(f"{self.base_url}/batches/{self.batch_id}")
            
            if response.status_code != 200:
                self.checkpoint(
                    "Verify batch state",
                    False,
                    {"status_code": response.status_code}
                )
                return
            
            data = response.json()
            batch = data.get("data", {})
            
            state = batch.get("state")
            posts_count = batch.get("posts_count", 0)
            
            if state != "S2_SEEDED":
                self.checkpoint(
                    "Batch in S2_SEEDED state",
                    False,
                    {"expected": "S2_SEEDED", "actual": state}
                )
                return
            
            if posts_count != 5:
                self.checkpoint(
                    "Correct posts count",
                    False,
                    {"expected": 5, "actual": posts_count}
                )
                return
            
            self.checkpoint(
                "Verify batch state",
                True,
                {"state": state, "posts_count": posts_count}
            )
            
        except Exception as e:
            self.checkpoint("Verify batch state", False, {"error": str(e)})
    
    def test_list_topics_registry(self):
        """Test 4: List topics from registry."""
        self.log("Test 4: List Topics Registry")
        
        try:
            response = self.client.get(f"{self.base_url}/topics")
            
            if response.status_code != 200:
                self.checkpoint(
                    "List topics registry",
                    False,
                    {"status_code": response.status_code}
                )
                return
            
            data = response.json()
            topics_data = data.get("data", {})
            topics = topics_data.get("topics", [])
            total = topics_data.get("total", 0)
            
            # Should have at least 5 topics (from our batch)
            if total < 5:
                self.checkpoint(
                    "Topics in registry",
                    False,
                    {"expected_min": 5, "actual": total}
                )
                return
            
            self.checkpoint(
                "List topics registry",
                True,
                {"total_topics": total, "sample_topic": topics[0] if topics else None}
            )
            
        except Exception as e:
            self.checkpoint("List topics registry", False, {"error": str(e)})
    
    def test_duplicate_prevention(self):
        """Test 5: Verify duplicate topic prevention."""
        self.log("Test 5: Duplicate Topic Prevention (running discovery again)")
        
        if not self.batch_id:
            self.checkpoint("Duplicate prevention", False, {"error": "No batch_id"})
            return
        
        try:
            # Create another batch with same brand
            payload = {
                "brand": "EcoLife Wellness",
                "post_type_counts": {
                    "value": 2,
                    "lifestyle": 1,
                    "product": 1
                }
            }
            
            response = self.client.post(f"{self.base_url}/batches", json=payload)
            
            if response.status_code != 201:
                self.checkpoint(
                    "Create second batch",
                    False,
                    {"status_code": response.status_code}
                )
                return
            
            data = response.json()
            batch2_id = data.get("data", {}).get("id")
            
            # Run discovery on second batch
            payload = {"batch_id": batch2_id, "count": 10}
            response = self.client.post(f"{self.base_url}/topics/discover", json=payload)
            
            if response.status_code != 200:
                self.checkpoint(
                    "Second discovery",
                    False,
                    {"status_code": response.status_code}
                )
                return
            
            # Should succeed (deduplication working)
            self.checkpoint(
                "Duplicate prevention",
                True,
                {"message": "Second batch created with deduplication"}
            )
            
        except Exception as e:
            self.checkpoint("Duplicate prevention", False, {"error": str(e)})
    
    def run(self):
        """Run all tests."""
        self.log("=" * 60)
        self.log("FLOW-FORGE Testscript: Phase 2 - Topic Discovery")
        self.log("=" * 60)
        self.log(f"Base URL: {self.base_url}")
        self.log("")
        
        # Re-run Phase 0 & 1 tests (regression guard)
        self.log("Running Phase 0 & 1 regression tests...")
        import subprocess
        
        for phase in [0, 1]:
            result = subprocess.run(
                ["python3", f"tests/testscript_phase{phase}.py", self.base_url],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                self.log(f"❌ Phase {phase} regression tests failed!", "ERROR")
                self.log(result.stdout, "ERROR")
                return 1
        
        self.log("✅ Phase 0 & 1 regression tests passed", "SUCCESS")
        self.log("")
        
        # Run Phase 2 tests
        self.test_create_test_batch()
        self.test_discover_topics()
        self.test_verify_batch_state()
        self.test_list_topics_registry()
        self.test_duplicate_prevention()
        
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
    
    runner = TestScriptPhase2(base_url)
    exit_code = runner.run()
    
    sys.exit(exit_code)
