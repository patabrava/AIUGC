#!/usr/bin/env python3
"""
Video Recovery Script
Recovers paid videos that were submitted but failed to update in Supabase.
Per Constitution § X: Hypothesis-Driven Debugging
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.adapters.sora_client import get_sora_client
from app.adapters.imagekit_client import get_imagekit_client
from app.adapters.supabase_client import get_supabase
from app.core.logging import configure_logging, get_logger

try:
    from app.adapters.veo_client import get_veo_client  # type: ignore
    _veo_available = True
except Exception as import_error:  # pragma: no cover - defensive guard for Python 3.9
    _veo_available = False
    def get_veo_client():  # type: ignore
        raise RuntimeError("VEO client unavailable: " + str(import_error))

configure_logging()
logger = get_logger(__name__)


def find_recovery_files():
    """Find all recovery log files."""
    recovery_dir = Path(__file__).parent
    return sorted(recovery_dir.glob("video_recovery_*.jsonl"))


def read_recovery_records(file_path):
    """Read recovery records from JSONL file."""
    records = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def recover_sora_video(record):
    """Recover a Sora video."""
    post_id = record["post_id"]
    operation_id = record["operation_id"]
    correlation_id = record["correlation_id"]
    
    logger.info(
        "recovering_sora_video",
        post_id=post_id,
        operation_id=operation_id,
        correlation_id=correlation_id
    )
    
    sora_client = get_sora_client()
    
    # Check status
    status_result = sora_client.check_video_status(
        video_id=operation_id,
        correlation_id=correlation_id
    )
    
    if not status_result.get("done"):
        logger.info(
            "sora_video_still_processing",
            post_id=post_id,
            operation_id=operation_id,
            status=status_result.get("status")
        )
        return False
    
    if status_result.get("status") == "failed":
        logger.error(
            "sora_video_failed",
            post_id=post_id,
            operation_id=operation_id,
            error=status_result.get("error")
        )
        return False
    
    # Download video
    video_bytes = sora_client.download_video(
        video_id=operation_id,
        correlation_id=correlation_id
    )
    
    # Upload to ImageKit
    imagekit = get_imagekit_client()
    upload_result = imagekit.upload_video(
        video_bytes=video_bytes,
        file_name=f"recovered_{post_id}.mp4",
        correlation_id=correlation_id
    )
    
    # Update Supabase
    supabase = get_supabase().client
    supabase.table("posts").update({
        "video_operation_id": operation_id,
        "video_status": "completed",
        "video_url": upload_result["url"],
        "video_metadata": {
            "recovered": True,
            "recovery_timestamp": datetime.utcnow().isoformat(),
            "imagekit_file_id": upload_result.get("file_id"),
            "provider": "sora_2_pro",
        }
    }).eq("id", post_id).execute()
    
    logger.info(
        "sora_video_recovered",
        post_id=post_id,
        operation_id=operation_id,
        video_url=upload_result["url"]
    )
    
    return True


def recover_veo_video(record):
    """Recover a VEO video."""
    if not _veo_available:
        logger.warning(
            "veo_recovery_skipped",
            post_id=record.get("post_id"),
            operation_id=record.get("operation_id"),
            message="VEO client unavailable on this runtime"
        )
        return False
    
    post_id = record["post_id"]
    operation_id = record["operation_id"]
    correlation_id = record["correlation_id"]
    
    logger.info(
        "recovering_veo_video",
        post_id=post_id,
        operation_id=operation_id,
        correlation_id=correlation_id
    )
    
    veo_client = get_veo_client()
    
    # Check status
    status_result = veo_client.check_operation_status(
        operation_id=operation_id,
        correlation_id=correlation_id
    )
    
    if not status_result.get("done"):
        logger.info(
            "veo_video_still_processing",
            post_id=post_id,
            operation_id=operation_id,
            status=status_result.get("status")
        )
        return False
    
    video_data = status_result.get("video_data")
    if not video_data or not video_data.get("video_uri"):
        logger.error(
            "veo_video_no_uri",
            post_id=post_id,
            operation_id=operation_id
        )
        return False
    
    # Download video
    video_bytes = veo_client.download_video(
        video_uri=video_data["video_uri"],
        correlation_id=correlation_id
    )
    
    # Upload to ImageKit
    imagekit = get_imagekit_client()
    upload_result = imagekit.upload_video(
        video_bytes=video_bytes,
        file_name=f"recovered_{post_id}.mp4",
        correlation_id=correlation_id
    )
    
    # Update Supabase
    supabase = get_supabase().client
    supabase.table("posts").update({
        "video_operation_id": operation_id,
        "video_status": "completed",
        "video_url": upload_result["url"],
        "video_metadata": {
            "recovered": True,
            "recovery_timestamp": datetime.utcnow().isoformat(),
            "imagekit_file_id": upload_result.get("file_id"),
            "provider": "veo_3_1",
        }
    }).eq("id", post_id).execute()
    
    logger.info(
        "veo_video_recovered",
        post_id=post_id,
        operation_id=operation_id,
        video_url=upload_result["url"]
    )
    
    return True


def main():
    recovery_files = find_recovery_files()
    
    if not recovery_files:
        print("No recovery files found.")
        return 0
    
    print(f"Found {len(recovery_files)} recovery file(s)")
    
    total_records = 0
    recovered = 0
    failed = 0
    still_processing = 0
    
    for file_path in recovery_files:
        print(f"\nProcessing: {file_path.name}")
        records = read_recovery_records(file_path)
        total_records += len(records)
        
        for record in records:
            provider = record.get("provider", "sora_2_pro")
            
            try:
                if provider in {"sora_2", "sora_2_pro"}:
                    success = recover_sora_video(record)
                elif provider == "veo_3_1":
                    success = recover_veo_video(record)
                else:
                    logger.error("unknown_provider", provider=provider, record=record)
                    failed += 1
                    continue
                
                if success:
                    recovered += 1
                else:
                    still_processing += 1
                    
            except Exception as e:
                logger.exception(
                    "recovery_failed",
                    post_id=record.get("post_id"),
                    operation_id=record.get("operation_id"),
                    error=str(e)
                )
                failed += 1
    
    print("\n=== Recovery Summary ===")
    print(f"Total records: {total_records}")
    print(f"Recovered: {recovered}")
    print(f"Still processing: {still_processing}")
    print(f"Failed: {failed}")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
