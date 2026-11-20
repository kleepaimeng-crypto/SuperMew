"""验收脚本显式提供受控默认配置；不是另一条产品配置入口。"""

MODEL_ENVIRONMENT_KEYS = (
    "MINIMEW_DEFAULT_MODEL_ADAPTER",
    "MINIMEW_DEFAULT_MODEL_NAME",
    "MINIMEW_DEFAULT_MODEL_BASE_URL",
    "MINIMEW_DEFAULT_MODEL_MAX_TOKENS",
    "MINIMEW_DEFAULT_MODEL_TIMEOUT",
)


def model_environment(configuration, *, adapter):
    return dict(
        zip(
            MODEL_ENVIRONMENT_KEYS,
            (
                adapter,
                configuration.model_name,
                configuration.base_url,
                str(configuration.max_output_tokens),
                str(configuration.timeout_seconds),
            ),
            strict=True,
        )
    )
