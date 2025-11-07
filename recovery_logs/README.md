# Video Recovery Logs

## Purpose
When a paid video is submitted to Sora/VEO but the Supabase database update fails, the operation_id is logged here to prevent losing paid content.

## Log Format
Each line in `video_recovery_YYYYMMDD.jsonl` contains:
```json
{
  "timestamp": "2025-11-07T10:44:00.000000",
  "post_id": "uuid",
  "operation_id": "video_xxx or operations/xxx",
  "provider": "sora_2_pro",
  "correlation_id": "correlation_xxx",
  "status": "db_update_failed",
  "message": "Video submitted to provider but database update failed..."
}
```

## Recovery Process
1. Run the recovery script: `python3 recovery_logs/recover_videos.py`
2. The script will:
   - Poll provider status for each operation_id
   - Download completed videos
   - Upload to ImageKit
   - Update Supabase with correct metadata
3. Check logs for `recovery_completed` events

## Manual Recovery
If automated recovery fails:
1. Copy the operation_id from the recovery log
2. Use provider's API/dashboard to download the video
3. Upload to ImageKit manually
4. Update the `posts` table with the ImageKit URL
