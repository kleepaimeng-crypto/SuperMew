"""执行资格信号必须穿过工具边界，由 Run 执行器处理。"""


class OwnershipLost(Exception):
    pass


class CancellationRequested(Exception):
    pass


class ExecutionStorageUnavailable(Exception):
    pass
