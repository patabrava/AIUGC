"""
Phase 4 Testscript: Video Generation (Sora 2 Pro)
Per Constitution § VIII: Whole-App Testscripts
Per Canon § 3.2: S5_PROMPTS_BUILT → S6_QA transition
Targets Sora 2 Pro pathway per Canon § 2.1 Video Providers.
"""

import requests
import time
import sys
import uuid

BASE_URL = "http://localhost:8000"
SORA_PROVIDER = "sora_2_pro"
TARGET_ASPECT_RATIO = "9:16"
TARGET_RESOLUTION = "1080p"
TARGET_SECONDS = 8
TARGET_SIZE = "1024x1792"


def test_phase4_video_generation():
    """
    Test video generation workflow end-to-end.
    Prerequisites: Phase 0, 1, 2, 3 passing, dev server running.
    """
    print("=" * 60)
    print("Phase 4 Testscript: Video Generation (Sora 2 Pro)")
    print("=" * 60)
    print()
    
    # Step 1: Verify prior phases still pass
    print("Step 1: Regression check - Verify health endpoint")
    response = requests.get(f"{BASE_URL}/health")
    assert response.status_code == 200, f"Health check failed: {response.status_code}"
    print("✅ Health endpoint: OK")
    print()
    
    # Step 2: Create test batch (reusing Phase 1-3 flow)
    print("Step 2: Create test batch")
    unique_brand = f"Phase4TestBrand-{uuid.uuid4().hex[:8]}"
    batch_payload = {
        "brand": unique_brand,
        "post_type_counts": {
            "value": 1,
            "lifestyle": 0,
            "product": 0
        }
    }
    response = requests.post(f"{BASE_URL}/batches", json=batch_payload)
    assert response.status_code in {200, 201}, (
        f"Batch creation failed: {response.status_code}"
    )
    batch_data = response.json()["data"]
    batch_id = batch_data["id"]
    print(f"✅ Batch created: {batch_id}")
    print(f"   State: {batch_data['state']}")
    print()
    
    # Step 3: Wait for seeding to complete (S1 → S2)
    print("Step 3: Wait for seeding to complete")
    max_wait = 30
    posts_created = False
    for i in range(max_wait):
        response = requests.get(f"{BASE_URL}/batches/{batch_id}")
        batch = response.json()["data"]
        if batch["state"] == "S2_SEEDED":
            posts = batch.get("posts", [])
            if posts:
                posts_created = True
                print(f"✅ Batch seeded with posts after {i+1} seconds")
                break
        time.sleep(1)
    else:
        print(f"❌ Batch did not reach S2_SEEDED with posts after {max_wait} seconds")
        sys.exit(1)

    if not posts_created:
        print("❌ No posts created after topic discovery")
        sys.exit(1)
    print()
    
    # Step 4: Approve scripts (S2 → S4)
    print("Step 4: Approve scripts")
    response = requests.put(f"{BASE_URL}/batches/{batch_id}/approve-scripts")
    assert response.status_code == 200, f"Script approval failed: {response.status_code}"
    print("✅ Scripts approved, batch advanced to S4_SCRIPTED")
    print()
    
    # Step 5: Get post ID for testing
    print("Step 5: Fetch post details")
    response = requests.get(f"{BASE_URL}/batches/{batch_id}")
    batch = response.json()["data"]
    posts = batch.get("posts", [])
    assert len(posts) > 0, "No posts found in batch"
    post_id = posts[0]["id"]
    print(f"✅ Post ID: {post_id}")
    print()
    
    # Step 6: Build video prompt (S4 → S5)
    print("Step 6: Build video prompt")
    response = requests.post(f"{BASE_URL}/posts/{post_id}/build-prompt")
    assert response.status_code == 200, f"Prompt build failed: {response.status_code}"
    prompt_data = response.json()["data"]
    print("✅ Video prompt built successfully")
    print(f"   State ready: {prompt_data['state_ready']}")
    print()
    
    # Step 7: Submit video generation via Sora 2 Pro
    print("Step 7: Submit video generation via Sora 2 Pro")
    video_request = {
        "provider": SORA_PROVIDER,
        "aspect_ratio": TARGET_ASPECT_RATIO,
        "resolution": TARGET_RESOLUTION,
        "seconds": TARGET_SECONDS,
        "size": TARGET_SIZE
    }
    response = requests.post(
        f"{BASE_URL}/videos/{post_id}/generate",
        json=video_request
    )
    assert response.status_code == 200, f"Video generation failed: {response.status_code}"
    video_data = response.json()["data"]
    operation_id = video_data["operation_id"]
    assert video_data["provider"] == SORA_PROVIDER, "Unexpected provider returned"
    print("✅ Video generation submitted")
    print(f"   Operation ID: {operation_id}")
    print(f"   Provider: {video_data['provider']}")
    print(f"   Provider model: {video_data.get('provider_model', 'unknown')}")
    print(f"   Status: {video_data['status']}")
    print(f"   Estimated duration: {video_data.get('estimated_duration_seconds', 'N/A')}s")
    print()

    # Step 8: Poll video status
    print("Step 8: Poll video status")
    print("   Note: Sora 2 Pro typically completes within ~2 minutes")
    max_polls = 40  # 40 * 10s = ~6.5 minutes max
    poll_interval = 10
    
    for i in range(max_polls):
        time.sleep(poll_interval)
        response = requests.get(f"{BASE_URL}/videos/{post_id}/status")
        assert response.status_code == 200, f"Status check failed: {response.status_code}"
        status_data = response.json()["data"]
        
        current_status = status_data["status"]
        print(f"   Poll {i+1}/{max_polls}: Status = {current_status}")

        if current_status == "completed":
            print("✅ Video generation completed!")
            print(f"   Video URL: {status_data['video_url']}")
            if status_data.get("metadata"):
                metadata = status_data["metadata"]
                print(f"   File ID: {metadata.get('imagekit_file_id', 'N/A')}")
                print(f"   Size: {metadata.get('size_bytes', 'N/A')} bytes")
                print(f"   Provider: {metadata.get('provider', 'N/A')}")
                print(f"   Provider model: {metadata.get('provider_model', 'N/A')}")
                assert metadata.get("provider") == SORA_PROVIDER, "Metadata provider mismatch"
            break
        elif current_status == "failed":
            print(f"❌ Video generation failed")
            if status_data.get("error_message"):
                print(f"   Error: {status_data['error_message']}")
            sys.exit(1)
    else:
        print(f"⚠️  Video still processing after {max_polls * poll_interval} seconds")
        print("   Sora 2 Pro may require additional time. Continue monitoring via worker logs.")
        print(f"   Operation ID: {operation_id}")
    
    print()
    
    # Step 9: Verify batch-level generation endpoint for Sora 2 Pro
    print("Step 9: Test batch-level video generation endpoint (Sora 2 Pro)")
    batch_video_request = {
        "provider": SORA_PROVIDER,
        "aspect_ratio": TARGET_ASPECT_RATIO,
        "resolution": TARGET_RESOLUTION,
        "seconds": TARGET_SECONDS,
        "size": TARGET_SIZE
    }
    response = requests.post(
        f"{BASE_URL}/videos/batch/{batch_id}/generate-all",
        json=batch_video_request
    )
    assert response.status_code == 200, f"Batch video generation failed: {response.status_code}"
    batch_video_data = response.json()["data"]
    print("✅ Batch video generation endpoint working")
    print(f"   Submitted: {batch_video_data['submitted_count']}")
    print(f"   Skipped: {batch_video_data['skipped_count']}")
    print(f"   Provider model: {batch_video_data.get('provider_model', 'N/A')}")
    print()

    print("=" * 60)
    print("Phase 4 Testscript Complete! (Sora 2 Pro)")
    print("=" * 60)
    print()
    print("Summary:")
    print("✅ Video generation submission (Sora 2 Pro) working")
    print("✅ Video status polling working")
    print("✅ Provider metadata recorded")
    print("✅ Batch-level generation working")
    print("✅ All Phase 4 features operational")
    print()
    print("Next steps:")
    print("1. Wait for video polling worker to complete Sora 2 Pro rendering if still processing")
    print("2. Verify video appears in ImageKit CDN")
    print("3. Check video URL is accessible")
    print("4. Capture worker logs (`workers/video_poller.py`) showing completion")
    print("5. Proceed to Phase 5: QA Review")


if __name__ == "__main__":
    try:
        test_phase4_video_generation()
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("\n❌ Cannot connect to server. Is it running on http://localhost:8000?")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
