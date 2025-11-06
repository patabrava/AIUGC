"""
FLOW-FORGE Video Generation Diagnostic Tool
Comprehensive debugging for video generation request chain.
Per Constitution § X: Hypothesis-Driven Debugging
"""

import sys
import os
import json
import traceback
from typing import Dict, Any, Optional
import httpx

# Add parent directory to path for app imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Test configuration
BASE_URL = "http://127.0.0.1:8000"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print('=' * 80)


def print_check(passed: bool, message: str, details: Optional[str] = None) -> None:
    """Print a check result."""
    symbol = "✅" if passed else "❌"
    print(f"{symbol} {message}")
    if details:
        print(f"   {details}")


def diagnose_environment() -> Dict[str, Any]:
    """Diagnose environment configuration."""
    print_section("PHASE 1: Environment Configuration")
    
    results = {
        "passed": True,
        "checks": []
    }
    
    try:
        from app.core.config import get_settings
        settings = get_settings()
        
        # Check Google AI API Key
        has_key = bool(settings.google_ai_api_key)
        print_check(has_key, "GOOGLE_AI_API_KEY present", 
                   f"Length: {len(settings.google_ai_api_key) if has_key else 0}")
        results["checks"].append({"name": "google_api_key", "passed": has_key})
        
        if not has_key:
            results["passed"] = False
        
        # Check Supabase configuration
        has_supabase = bool(settings.supabase_url and settings.supabase_key)
        print_check(has_supabase, "Supabase configuration present",
                   f"URL: {settings.supabase_url[:30]}...")
        results["checks"].append({"name": "supabase_config", "passed": has_supabase})
        
        # Check ImageKit configuration
        has_imagekit = bool(settings.imagekit_private_key)
        print_check(has_imagekit, "ImageKit configuration present")
        results["checks"].append({"name": "imagekit_config", "passed": has_imagekit})
        
    except Exception as e:
        print_check(False, f"Environment check failed: {str(e)}")
        results["passed"] = False
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
    
    return results


def diagnose_api_connectivity() -> Dict[str, Any]:
    """Test API server connectivity."""
    print_section("PHASE 2: API Server Connectivity")
    
    results = {
        "passed": True,
        "checks": []
    }
    
    try:
        # Test health endpoint
        response = httpx.get(f"{BASE_URL}/health", timeout=5.0)
        passed = response.status_code == 200
        print_check(passed, f"Health endpoint: {response.status_code}",
                   f"Response: {response.text[:100]}")
        results["checks"].append({"name": "health", "passed": passed, "status": response.status_code})
        
        if not passed:
            results["passed"] = False
            
    except httpx.ConnectError:
        print_check(False, "API server not running", 
                   f"Could not connect to {BASE_URL}")
        results["passed"] = False
        results["error"] = "Connection failed"
    except Exception as e:
        print_check(False, f"Connectivity check failed: {str(e)}")
        results["passed"] = False
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
    
    return results


def diagnose_veo_direct() -> Dict[str, Any]:
    """Test VEO API directly."""
    print_section("PHASE 3: VEO API Direct Test")
    
    results = {
        "passed": True,
        "checks": [],
        "response_data": None
    }
    
    try:
        from app.core.config import get_settings
        settings = get_settings()
        
        api_key = settings.google_ai_api_key
        
        if not api_key:
            print_check(False, "GOOGLE_AI_API_KEY not configured")
            results["passed"] = False
            return results
        
        # Minimal VEO test payload
        test_payload = {
            "instances": [
                {
                    "prompt": "Test prompt for diagnostic connectivity check",
                    "config": {
                        "aspect_ratio": "9:16",
                        "duration_seconds": 6
                    }
                }
            ]
        }
        
        print(f"\n📤 Sending test request to VEO API...")
        print(f"   Endpoint: {GEMINI_BASE}/models/veo-3.1-generate-preview:predictLongRunning")
        print(f"   Payload: {json.dumps(test_payload, indent=2)}")
        
        response = httpx.post(
            f"{GEMINI_BASE}/models/veo-3.1-generate-preview:predictLongRunning",
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json"
            },
            json=test_payload,
            timeout=30.0
        )
        
        print(f"\n📥 Response received:")
        print(f"   Status: {response.status_code}")
        print(f"   Headers: {dict(response.headers)}")
        print(f"   Body: {response.text[:500]}")
        
        passed = response.status_code in [200, 201]
        print_check(passed, f"VEO API response: {response.status_code}")
        
        results["status_code"] = response.status_code
        results["response_text"] = response.text
        results["response_headers"] = dict(response.headers)
        
        if passed:
            try:
                data = response.json()
                results["response_data"] = data
                operation_name = data.get("name")
                print_check(bool(operation_name), "Operation ID received",
                           f"ID: {operation_name}")
            except Exception as e:
                print_check(False, f"Failed to parse response: {str(e)}")
                results["passed"] = False
        else:
            results["passed"] = False
            try:
                error_data = response.json()
                print(f"\n❌ Error details: {json.dumps(error_data, indent=2)}")
                results["error_data"] = error_data
            except:
                pass
                
    except Exception as e:
        print_check(False, f"VEO direct test failed: {str(e)}")
        results["passed"] = False
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        print(f"\n🔍 Full traceback:\n{traceback.format_exc()}")
    
    return results


def diagnose_veo_adapter() -> Dict[str, Any]:
    """Test VEO adapter in isolation."""
    print_section("PHASE 4: VEO Adapter Test")
    
    results = {
        "passed": True,
        "checks": []
    }
    
    try:
        from app.adapters.veo_client import get_veo_client
        
        veo_client = get_veo_client()
        print_check(True, "VEO client initialized")
        
        # Test minimal submission
        test_result = veo_client.submit_video_generation(
            prompt="Diagnostic test prompt for adapter verification",
            correlation_id="diagnostic_test",
            aspect_ratio="9:16",
            resolution="720p"
        )
        
        passed = "operation_id" in test_result
        print_check(passed, "Adapter submission successful",
                   f"Operation: {test_result.get('operation_id', 'N/A')}")
        results["operation_result"] = test_result
        
        if not passed:
            results["passed"] = False
            
    except Exception as e:
        print_check(False, f"Adapter test failed: {str(e)}")
        results["passed"] = False
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        print(f"\n🔍 Full traceback:\n{traceback.format_exc()}")
    
    return results


def diagnose_post_preparation() -> Dict[str, Any]:
    """Test post creation and prompt building."""
    print_section("PHASE 5: Post Preparation Test")
    
    results = {
        "passed": True,
        "checks": [],
        "post_id": None,
        "batch_id": None
    }
    
    try:
        # Create test batch
        response = httpx.post(
            f"{BASE_URL}/batches",
            json={
                "brand": "Diagnostic Test Brand",
                "post_type_counts": {
                    "value": 1,
                    "lifestyle": 0,
                    "product": 0
                }
            },
            timeout=30.0
        )
        
        if response.status_code != 201:
            print_check(False, f"Batch creation failed: {response.status_code}",
                       f"Response: {response.text}")
            results["passed"] = False
            return results
        
        batch_data = response.json()["data"]
        batch_id = batch_data["id"]
        results["batch_id"] = batch_id
        print_check(True, f"Batch created: {batch_id}")
        
        # Wait for seeding
        import time
        for i in range(10):
            time.sleep(1)
            response = httpx.get(f"{BASE_URL}/batches/{batch_id}")
            batch = response.json()["data"]
            if batch["state"] == "S2_SEEDED" and batch.get("posts"):
                break
        
        if batch["state"] != "S2_SEEDED":
            print_check(False, f"Batch not seeded: {batch['state']}")
            results["passed"] = False
            return results
        
        print_check(True, "Batch seeded successfully")
        
        # Approve scripts
        response = httpx.put(f"{BASE_URL}/batches/{batch_id}/approve-scripts")
        if response.status_code not in [200, 204]:
            print_check(False, f"Script approval failed: {response.status_code}")
            results["passed"] = False
            return results
        
        print_check(True, "Scripts approved")
        
        # Get post ID
        response = httpx.get(f"{BASE_URL}/batches/{batch_id}")
        batch = response.json()["data"]
        posts = batch.get("posts", [])
        
        if not posts:
            print_check(False, "No posts in batch")
            results["passed"] = False
            return results
        
        post_id = posts[0]["id"]
        results["post_id"] = post_id
        print_check(True, f"Post ID obtained: {post_id}")
        
        # Build prompt
        response = httpx.post(f"{BASE_URL}/posts/{post_id}/build-prompt", timeout=30.0)
        if response.status_code != 200:
            print_check(False, f"Prompt build failed: {response.status_code}",
                       f"Response: {response.text}")
            results["passed"] = False
            return results
        
        prompt_data = response.json()["data"]
        print_check(True, "Video prompt built",
                   f"State: {prompt_data.get('state_ready')}")
        
        results["prompt_data"] = prompt_data
        
    except Exception as e:
        print_check(False, f"Post preparation failed: {str(e)}")
        results["passed"] = False
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        print(f"\n🔍 Full traceback:\n{traceback.format_exc()}")
    
    return results


def diagnose_video_endpoint(post_id: str) -> Dict[str, Any]:
    """Test video generation endpoint with detailed logging."""
    print_section("PHASE 6: Video Generation Endpoint Test")
    
    results = {
        "passed": True,
        "checks": []
    }
    
    try:
        request_payload = {
            "provider": "veo_3_1",
            "aspect_ratio": "9:16",
            "resolution": "720p"
        }
        
        print(f"\n📤 Sending video generation request:")
        print(f"   POST {BASE_URL}/videos/{post_id}/generate")
        print(f"   Payload: {json.dumps(request_payload, indent=2)}")
        
        response = httpx.post(
            f"{BASE_URL}/videos/{post_id}/generate",
            json=request_payload,
            timeout=30.0
        )
        
        print(f"\n📥 Response received:")
        print(f"   Status: {response.status_code}")
        print(f"   Headers: {dict(response.headers)}")
        print(f"   Body: {response.text[:1000]}")
        
        passed = response.status_code == 200
        print_check(passed, f"Video endpoint response: {response.status_code}")
        
        results["status_code"] = response.status_code
        results["response_text"] = response.text
        results["response_headers"] = dict(response.headers)
        
        if passed:
            try:
                data = response.json()["data"]
                results["response_data"] = data
                print_check(True, "Video generation submitted",
                           f"Operation: {data.get('operation_id')}")
            except Exception as e:
                print_check(False, f"Failed to parse response: {str(e)}")
                results["passed"] = False
        else:
            results["passed"] = False
            
    except Exception as e:
        print_check(False, f"Endpoint test failed: {str(e)}")
        results["passed"] = False
        results["error"] = str(e)
        results["traceback"] = traceback.format_exc()
        print(f"\n🔍 Full traceback:\n{traceback.format_exc()}")
    
    return results


def main():
    """Run full diagnostic suite."""
    print("\n" + "=" * 80)
    print("  FLOW-FORGE VIDEO GENERATION DIAGNOSTIC TOOL")
    print("  Comprehensive debugging for video generation request chain")
    print("=" * 80)
    
    report = {
        "phases": {},
        "overall_passed": True
    }
    
    # Phase 1: Environment
    env_results = diagnose_environment()
    report["phases"]["environment"] = env_results
    if not env_results["passed"]:
        report["overall_passed"] = False
        print("\n❌ Environment checks failed. Fix configuration before proceeding.")
        return report
    
    # Phase 2: API Connectivity
    api_results = diagnose_api_connectivity()
    report["phases"]["api_connectivity"] = api_results
    if not api_results["passed"]:
        report["overall_passed"] = False
        print("\n❌ API connectivity failed. Ensure server is running.")
        return report
    
    # Phase 3: VEO Direct
    veo_direct_results = diagnose_veo_direct()
    report["phases"]["veo_direct"] = veo_direct_results
    if not veo_direct_results["passed"]:
        report["overall_passed"] = False
        print("\n⚠️  VEO direct test failed. Check API key and payload format.")
    
    # Phase 4: VEO Adapter
    adapter_results = diagnose_veo_adapter()
    report["phases"]["veo_adapter"] = adapter_results
    if not adapter_results["passed"]:
        report["overall_passed"] = False
        print("\n⚠️  VEO adapter test failed.")
    
    # Phase 5: Post Preparation
    prep_results = diagnose_post_preparation()
    report["phases"]["post_preparation"] = prep_results
    if not prep_results["passed"]:
        report["overall_passed"] = False
        print("\n❌ Post preparation failed.")
        return report
    
    # Phase 6: Video Endpoint
    if prep_results.get("post_id"):
        endpoint_results = diagnose_video_endpoint(prep_results["post_id"])
        report["phases"]["video_endpoint"] = endpoint_results
        if not endpoint_results["passed"]:
            report["overall_passed"] = False
    
    # Final summary
    print_section("DIAGNOSTIC SUMMARY")
    
    for phase_name, phase_results in report["phases"].items():
        status = "✅ PASSED" if phase_results.get("passed") else "❌ FAILED"
        print(f"{status} - {phase_name}")
        if not phase_results.get("passed") and phase_results.get("error"):
            print(f"   Error: {phase_results['error']}")
    
    print(f"\n{'=' * 80}")
    if report["overall_passed"]:
        print("✅ ALL DIAGNOSTICS PASSED")
    else:
        print("❌ DIAGNOSTICS FAILED - Review errors above")
    print('=' * 80)
    
    # Save detailed report
    with open("diagnostic_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n📄 Detailed report saved to: diagnostic_report.json")
    
    return report


if __name__ == "__main__":
    try:
        report = main()
        sys.exit(0 if report["overall_passed"] else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Diagnostic interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Diagnostic tool crashed: {str(e)}")
        print(traceback.format_exc())
        sys.exit(1)
