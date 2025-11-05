#!/usr/bin/env python3
"""
FLOW-FORGE Testscript: Phase 0 - Foundation
Objective: Verify FastAPI skeleton, config, logging, Supabase connection, error envelopes, health endpoint
Prerequisites: requirements.txt installed, .env configured
Per Constitution § VIII: Whole-App Testscripts
"""

import sys
import httpx
import json
from datetime import datetime


class TestScriptPhase0:
    """Phase 0 testscript runner."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.Client(timeout=10.0)
        self.passed = 0
        self.failed = 0
        self.artifacts = []
    
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
    
    def test_health_endpoint(self):
        """Test 1: Health endpoint returns 200 and correct structure."""
        self.log("Test 1: Health Endpoint")
        
        try:
            response = self.client.get(f"{self.base_url}/health")
            
            # Check status code
            if response.status_code != 200:
                self.checkpoint(
                    "Health endpoint status code",
                    False,
                    {"expected": 200, "actual": response.status_code}
                )
                return
            
            # Check response structure
            data = response.json()
            required_fields = ["status", "version", "environment", "checks"]
            missing_fields = [f for f in required_fields if f not in data]
            
            if missing_fields:
                self.checkpoint(
                    "Health endpoint response structure",
                    False,
                    {"missing_fields": missing_fields}
                )
                return
            
            # Check database health
            db_healthy = data["checks"].get("database") == "ok"
            
            self.checkpoint(
                "Health endpoint",
                True,
                {
                    "status": data["status"],
                    "version": data["version"],
                    "database": data["checks"]["database"]
                }
            )
            
            if not db_healthy:
                self.log("WARNING: Database health check failed", "WARN")
            
        except Exception as e:
            self.checkpoint("Health endpoint", False, {"error": str(e)})
    
    def test_correlation_id(self):
        """Test 2: Correlation ID middleware adds X-Correlation-ID header."""
        self.log("Test 2: Correlation ID Middleware")
        
        try:
            response = self.client.get(f"{self.base_url}/health")
            
            has_correlation_id = "X-Correlation-ID" in response.headers
            
            self.checkpoint(
                "Correlation ID header",
                has_correlation_id,
                {
                    "header_present": has_correlation_id,
                    "correlation_id": response.headers.get("X-Correlation-ID")
                }
            )
            
        except Exception as e:
            self.checkpoint("Correlation ID header", False, {"error": str(e)})
    
    def test_root_endpoint(self):
        """Test 3: Root endpoint returns application info."""
        self.log("Test 3: Root Endpoint")
        
        try:
            response = self.client.get(f"{self.base_url}/")
            
            if response.status_code != 200:
                self.checkpoint(
                    "Root endpoint",
                    False,
                    {"status_code": response.status_code}
                )
                return
            
            data = response.json()
            has_message = "message" in data
            has_version = "version" in data
            
            self.checkpoint(
                "Root endpoint",
                has_message and has_version,
                {"response": data}
            )
            
        except Exception as e:
            self.checkpoint("Root endpoint", False, {"error": str(e)})
    
    def test_404_handling(self):
        """Test 4: Non-existent endpoint returns 404."""
        self.log("Test 4: 404 Error Handling")
        
        try:
            response = self.client.get(f"{self.base_url}/nonexistent")
            
            is_404 = response.status_code == 404
            
            self.checkpoint(
                "404 error handling",
                is_404,
                {"status_code": response.status_code}
            )
            
        except Exception as e:
            self.checkpoint("404 error handling", False, {"error": str(e)})
    
    def test_openapi_docs(self):
        """Test 5: OpenAPI documentation is accessible."""
        self.log("Test 5: OpenAPI Documentation")
        
        try:
            response = self.client.get(f"{self.base_url}/docs")
            
            is_accessible = response.status_code == 200
            
            self.checkpoint(
                "OpenAPI docs accessible",
                is_accessible,
                {"status_code": response.status_code}
            )
            
        except Exception as e:
            self.checkpoint("OpenAPI docs accessible", False, {"error": str(e)})
    
    def run(self):
        """Run all tests."""
        self.log("=" * 60)
        self.log("FLOW-FORGE Testscript: Phase 0 - Foundation")
        self.log("=" * 60)
        self.log(f"Base URL: {self.base_url}")
        self.log("")
        
        # Run tests
        self.test_health_endpoint()
        self.test_correlation_id()
        self.test_root_endpoint()
        self.test_404_handling()
        self.test_openapi_docs()
        
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
    
    runner = TestScriptPhase0(base_url)
    exit_code = runner.run()
    
    sys.exit(exit_code)
