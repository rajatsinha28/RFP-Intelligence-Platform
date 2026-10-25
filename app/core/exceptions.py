class AdvanceRAGError(Exception):
    """Base application exception."""


class ConfigurationError(AdvanceRAGError):
    pass


class DocumentParseError(AdvanceRAGError):
    pass


class UnsupportedDocumentError(DocumentParseError):
    pass


class UnsafeURLError(AdvanceRAGError):
    pass


class ExternalServiceError(AdvanceRAGError):
    pass


class CircuitOpenError(ExternalServiceError):
    pass


class InsufficientEvidenceError(AdvanceRAGError):
    pass
