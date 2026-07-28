class ApplicationError(Exception):
    status_code = 500


class ValidationError(ApplicationError):
    status_code = 400


class PayloadTooLargeError(ValidationError):
    status_code = 413


class AuthenticationError(ApplicationError):
    status_code = 401


class ResourceNotFoundError(ApplicationError):
    status_code = 404


class RateLimitError(ApplicationError):
    status_code = 429


class DatabaseError(ApplicationError):
    status_code = 500


class ExternalServiceError(ApplicationError):
    status_code = 502
