class KnowledgeBaseError(ValueError):
    """知识库错误仅携带可公开的错误码和中文提示。"""

    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
