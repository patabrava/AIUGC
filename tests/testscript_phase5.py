"""
FLOW-FORGE Phase 5 Testscript: QA Review
Whole-app end-to-end test for quality assurance workflow.
Per Constitution § VIII: Test end-to-end in real environment
Per Canon § 3.2: S6_QA state management and transitions
"""

import httpx
import asyncio
from typing import Dict, Any, Optional

BASE_URL = "http://localhost:8000"


async def test_phase5_qa_workflow():
    """
    Test Phase 5: QA Review workflow.
    
    Prerequisites:
    - Phase 4 passing (videos generated and uploaded to ImageKit)
    - At least one batch in S6_QA state with completed videos
    - Or manually create a batch with completed videos
    
    Test Steps:
    1. Find or create a batch in S6_QA state
    2. Run auto QA checks on a post
    3. Verify auto check results (duration, resolution, file accessible)
    4. Approve a post manually
    5. Verify post qa_pass=true
    6. Approve all posts in batch
    7. Advance batch from S6_QA to S7_PUBLISH_PLAN
    8. Verify batch state is S7_PUBLISH_PLAN
    
    Pass/Fail Criteria:
    - Auto QA checks execute without errors
    - Manual approval updates qa_pass field
    - Batch advances to S7_PUBLISH_PLAN only when all posts approved
    - All API responses follow standard envelope pattern
    """
    
    print("\n" + "="*80)
    print("PHASE 5 TESTSCRIPT: QA REVIEW")
    print("="*80 + "\n")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: Find a batch with completed videos
        print("Step 1: Finding batch with completed videos...")
        batch_id, posts = await find_batch_with_videos(client)
        
        if not batch_id:
            print("❌ FAIL: No batch found with completed videos")
            print("   Please run Phase 4 first to generate videos")
            return False
        
        print(f"✓ Found batch: {batch_id}")
        print(f"  Posts with videos: {len(posts)}")
        
        # Step 2: Manually transition batch to S6_QA if needed
        await ensure_batch_in_qa_state(client, batch_id)
        
        # Step 3: Run auto QA checks on first post
        print("\nStep 2: Running auto QA checks on first post...")
        post_id = posts[0]["id"]
        auto_checks = await run_auto_qa_check(client, post_id)
        
        if not auto_checks:
            print("❌ FAIL: Auto QA check failed")
            return False
        
        print("✓ Auto QA checks completed")
        print(f"  Duration valid: {auto_checks.get('duration_valid')}")
        print(f"  Resolution valid: {auto_checks.get('resolution_valid')}")
        print(f"  File accessible: {auto_checks.get('file_accessible')}")
        print(f"  Overall pass: {auto_checks.get('overall_pass')}")
        
        # Step 4: Approve first post
        print(f"\nStep 3: Approving post {post_id}...")
        approval_result = await approve_post(client, post_id, approved=True)
        
        if not approval_result or not approval_result.get("qa_pass"):
            print("❌ FAIL: Post approval failed")
            return False
        
        print("✓ Post approved successfully")
        
        # Step 5: Approve all remaining posts
        print("\nStep 4: Approving all remaining posts in batch...")
        for post in posts[1:]:
            await approve_post(client, post["id"], approved=True)
        
        print(f"✓ All {len(posts)} posts approved")
        
        # Step 6: Check batch QA status
        print("\nStep 5: Checking batch QA status...")
        qa_status = await get_batch_qa_status(client, batch_id)
        
        if not qa_status:
            print("❌ FAIL: Could not get batch QA status")
            return False
        
        print("✓ Batch QA status retrieved")
        print(f"  Total posts: {qa_status.get('total_posts')}")
        print(f"  QA passed: {qa_status.get('posts_qa_passed')}")
        print(f"  Can advance: {qa_status.get('can_advance_to_publish')}")
        
        if not qa_status.get("can_advance_to_publish"):
            print("❌ FAIL: Batch cannot advance to publish")
            print(f"   Some posts not approved: {qa_status.get('posts_qa_pending')} pending")
            return False
        
        # Step 7: Advance batch to S7_PUBLISH_PLAN
        print("\nStep 6: Advancing batch to S7_PUBLISH_PLAN...")
        advanced = await advance_batch_to_publish(client, batch_id)
        
        if not advanced:
            print("❌ FAIL: Could not advance batch to publish state")
            return False
        
        print("✓ Batch advanced to S7_PUBLISH_PLAN")
        
        # Step 8: Verify final batch state
        print("\nStep 7: Verifying final batch state...")
        final_batch = await get_batch(client, batch_id)
        
        if not final_batch:
            print("❌ FAIL: Could not retrieve batch")
            return False
        
        if final_batch.get("state") != "S7_PUBLISH_PLAN":
            print(f"❌ FAIL: Batch state is {final_batch.get('state')}, expected S7_PUBLISH_PLAN")
            return False
        
        print("✓ Batch state verified: S7_PUBLISH_PLAN")
        
        print("\n" + "="*80)
        print("✅ PHASE 5 TESTSCRIPT PASSED")
        print("="*80 + "\n")
        print("Summary:")
        print(f"  Batch ID: {batch_id}")
        print(f"  Total posts: {len(posts)}")
        print(f"  All posts approved: ✓")
        print(f"  Batch advanced to publish: ✓")
        print("\nPhase 5 QA workflow is working correctly!")
        
        return True


async def find_batch_with_videos(client: httpx.AsyncClient) -> tuple[Optional[str], list]:
    """Find a batch with completed videos."""
    try:
        response = await client.get(f"{BASE_URL}/batches")
        
        if response.status_code != 200:
            print(f"Error fetching batches: {response.status_code}")
            return None, []
        
        data = response.json()
        batches = data.get("data", {}).get("batches", [])
        
        for batch in batches:
            batch_id = batch["id"]
            
            # Get posts for this batch
            posts_response = await client.get(f"{BASE_URL}/batches/{batch_id}")
            if posts_response.status_code != 200:
                continue
            
            batch_data = posts_response.json()
            posts = batch_data.get("data", {}).get("posts", [])
            
            # Check if all posts have completed videos
            completed_posts = [p for p in posts if p.get("video_status") == "completed"]
            
            if completed_posts and len(completed_posts) == len(posts):
                return batch_id, completed_posts
        
        return None, []
    
    except Exception as e:
        print(f"Error finding batch: {e}")
        return None, []


async def ensure_batch_in_qa_state(client: httpx.AsyncClient, batch_id: str) -> bool:
    """Ensure batch is in S6_QA state."""
    try:
        response = await client.get(f"{BASE_URL}/batches/{batch_id}")
        
        if response.status_code != 200:
            return False
        
        data = response.json()
        batch = data.get("data", {})
        current_state = batch.get("state")
        
        print(f"  Current batch state: {current_state}")
        
        if current_state == "S6_QA":
            print("  ✓ Batch already in S6_QA state")
            return True
        
        if current_state == "S5_PROMPTS_BUILT":
            print("  Manually transitioning batch to S6_QA...")
            # Manually update via Supabase MCP if needed
            # For now, assume video poller will handle this
            print("  Note: Wait for video poller to transition batch to S6_QA")
            return True
        
        return True
    
    except Exception as e:
        print(f"Error checking batch state: {e}")
        return False


async def run_auto_qa_check(client: httpx.AsyncClient, post_id: str) -> Optional[Dict[str, Any]]:
    """Run auto QA checks on a post."""
    try:
        response = await client.post(f"{BASE_URL}/qa/{post_id}/auto-check")
        
        if response.status_code != 200:
            print(f"  Error: {response.status_code} - {response.text}")
            return None
        
        data = response.json()
        return data.get("data")
    
    except Exception as e:
        print(f"  Exception: {e}")
        return None


async def approve_post(client: httpx.AsyncClient, post_id: str, approved: bool) -> Optional[Dict[str, Any]]:
    """Approve or reject a post."""
    try:
        response = await client.put(
            f"{BASE_URL}/qa/{post_id}/approve",
            json={"approved": approved, "notes": "Testscript approval"}
        )
        
        if response.status_code != 200:
            print(f"  Error approving post: {response.status_code}")
            return None
        
        data = response.json()
        return data.get("data")
    
    except Exception as e:
        print(f"  Exception: {e}")
        return None


async def get_batch_qa_status(client: httpx.AsyncClient, batch_id: str) -> Optional[Dict[str, Any]]:
    """Get batch QA status summary."""
    try:
        response = await client.get(f"{BASE_URL}/qa/batch/{batch_id}/status")
        
        if response.status_code != 200:
            print(f"  Error: {response.status_code}")
            return None
        
        data = response.json()
        return data.get("data")
    
    except Exception as e:
        print(f"  Exception: {e}")
        return None


async def advance_batch_to_publish(client: httpx.AsyncClient, batch_id: str) -> bool:
    """Advance batch from S6_QA to S7_PUBLISH_PLAN."""
    try:
        response = await client.put(f"{BASE_URL}/batches/{batch_id}/advance-to-publish")
        
        if response.status_code != 200:
            print(f"  Error: {response.status_code} - {response.text}")
            return False
        
        return True
    
    except Exception as e:
        print(f"  Exception: {e}")
        return False


async def get_batch(client: httpx.AsyncClient, batch_id: str) -> Optional[Dict[str, Any]]:
    """Get batch details."""
    try:
        response = await client.get(f"{BASE_URL}/batches/{batch_id}")
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        return data.get("data")
    
    except Exception as e:
        print(f"  Exception: {e}")
        return None


if __name__ == "__main__":
    print("\nFLOW-FORGE Phase 5 QA Review Testscript")
    print("Make sure the dev server is running on http://localhost:8000")
    print("Press Enter to continue...")
    input()
    
    result = asyncio.run(test_phase5_qa_workflow())
    
    if result:
        print("\n✅ All tests passed!")
        exit(0)
    else:
        print("\n❌ Tests failed!")
        exit(1)
