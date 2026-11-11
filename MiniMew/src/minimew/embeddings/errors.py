class EmbeddingError(Exception):
    """调用方按稳定 code 处理；不将异常细节或本机路径作为公开消息。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
