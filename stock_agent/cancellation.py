from __future__ import annotations


class RunCancelledError(RuntimeError):
    def __init__(self, run_id: str, safe_point: str):
        super().__init__(f"run {run_id} cancelled at safe point {safe_point}")
        self.run_id = run_id
        self.safe_point = safe_point


class CancellationToken:
    def __init__(self, database, run_id: str):
        self.database = database
        self.run_id = run_id

    def requested(self) -> bool:
        return self.database.is_cancel_requested(self.run_id)

    def check(self, safe_point: str) -> None:
        if self.requested():
            self.database.acknowledge_cancellation(self.run_id)
            raise RunCancelledError(self.run_id, safe_point)
