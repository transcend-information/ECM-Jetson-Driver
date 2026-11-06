"""Live install stages driven by execution, separate from diagnostic stages."""
import time

TITLES = (
    'Checking installation requirements', 'Backing up affected files',
    'Preparing existing camera Drivers', 'Installing Driver and tuning files',
    'Updating boot configuration', 'Updating and checking module indexes',
    'Saving installation results and logs',
)


class InstallProgress:
    def __init__(self, report=None):
        self.report = report
        self.number = 0
        self.finished = False
        self.warning = False
        self.failure_mode = False
        self.failure_detail = None

    def emit(self, number, status, **fields):
        # Flush before durable logging: even a log failure is visible immediately.
        print('[%d/7] %s... %s' % (number, TITLES[number - 1], status), flush=True)
        if self.report is not None:
            self.report.event('install-progress', number=number, title=TITLES[number - 1],
                              status=status, monotonic_ns=time.monotonic_ns(), **fields)

    def start(self, number):
        if self.number == number:
            return
        self.done()
        self.number, self.finished, self.warning = number, False, False
        self.emit(number, 'Running')

    def done(self):
        if self.number and not self.finished:
            self.emit(self.number, 'Warning' if self.warning else 'Done')
            self.finished = True

    def prepare_commit(self):
        # Persist the final progress event while the transaction's commit gate
        # still protects against interruption. Display Done only after it clears.
        if self.report is not None:
            self.report.event('install-progress',number=7,title=TITLES[6],status='Done',
                              monotonic_ns=time.monotonic_ns(),commit_prepared=True)

    def committed(self):
        self.finished=True
        try:print('[7/7] '+TITLES[6]+'... Done',flush=True)
        except OSError:pass  # A closed display does not undo a durable commit.

    def fail(self, detail):
        if not self.number or self.failure_mode:
            return
        self.failure_mode=True;self.failure_detail=detail
        rows = ([(self.number, 'Failed')] if self.number<7 else []) + [(n, 'Skipped') for n in range(self.number + 1, 7)]
        for number, status in rows:
            try:
                self.emit(number, status, diagnostic=detail)
            except Exception:
                pass  # The primary storage error remains the reported failure.
        if self.number<7:
            self.number=7
            try:self.emit(7,'Running',purpose='Saving failure details; successful installation commit was not completed')
            except Exception:pass
        self.finished = False

    def failure_saved(self, saved, fallback=None):
        if not self.failure_mode:return
        detail=self.failure_detail or {}
        storage_failure=detail.get('error_code') in ('LOG_WRITE_FAILED','RESULT_PERSIST_FAILED','EXECUTOR_INCOMPLETE')
        status='Failed' if not saved or storage_failure else 'Warning' if detail.get('diagnostic_errors') else 'Done'
        self.emit(7,status,purpose='Failure record',installation_outcome=detail.get('outcome','FAILED'),fallback=fallback)
        self.finished=True
