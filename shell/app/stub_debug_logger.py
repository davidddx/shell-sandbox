class StubDebugLogger:
    def debug(self, *args):
        pass

debug_logger = StubDebugLogger()
