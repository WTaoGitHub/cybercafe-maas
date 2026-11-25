class ServiceError(Exception):
    def __init__(self, code, message, status=400, retryable=False, *, transient_before_grant=False):
        super().__init__(message)
        self.transient_before_grant = transient_before_grant
        self.code, self.message, self.status, self.retryable = code, message, status, retryable
