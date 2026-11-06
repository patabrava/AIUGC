"""One durable, nonblocking post-delivery evaluator slot; never submits video."""
import os
from app.core.logging import get_logger
from app.features.semantic_videos import queries

logger = get_logger(__name__)


class ReviewRepository:
    def claim(self, **kwargs):
        return queries.claim_optional_qa(**kwargs)

    def takes(self, run_id):
        return queries.list_attempts(run_id)

    def renew_run(self, **kwargs):
        return queries.renew_worker_lease(**kwargs)

    def finish(self, **kwargs):
        return queries.finish_optional_qa(**kwargs)


class OptionalVideoQAWorker:
    def __init__(self, *, stage_runner, heartbeat_factory, repo=None, worker_id=None):
        self.repo = repo or ReviewRepository()
        self.stage_runner = stage_runner
        self.heartbeat_factory = heartbeat_factory
        self.worker_id = worker_id or f'semantic-video-optional-{os.getpid()}'

    def tick(self):
        run = self.repo.claim(worker_id=self.worker_id, lease_seconds=120)
        if not run:
            return None
        heartbeat = self.heartbeat_factory(
            repo=self.repo, run_id=run['id'], worker_id=self.worker_id,
            lease_token=run['lease_token'], lease_seconds=120, interval_seconds=40,
        )
        logger.info('semantic_video_optional_qa_started', run_id=run['id'])
        heartbeat.start()
        try:
            try:
                report = self.stage_runner.run_optional_qa(run=run, takes=self.repo.takes(run['id']))
            except Exception:
                logger.exception('semantic_video_optional_qa_failed', run_id=run['id'])
                report = {'status': 'failed', 'checks': {}, 'findings': [
                    'Optional checks could not finish. The video remains available for review.',
                ]}
            self.repo.finish(
                run_id=run['id'], worker_id=self.worker_id, lease_token=run['lease_token'],
                delivery_sha256=run['final_caption_sha256'], report=report,
            )
            logger.info('semantic_video_optional_qa_finished', run_id=run['id'], status=report['status'])
            return report
        finally:
            heartbeat.stop()
            # Successful finish releases atomically. A lost process/DB response is
            # reconciled by durable status or reclaimed after expiry, never by replay.
            self.stage_runner.cleanup_run_workspace(run['id'])
