"""2026-11-19核对的官方预设。显示名与API ID分开，不自动替换已存配置。"""

PRESETS = (
    dict(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        models=("deepseek-flash", "deepseek-v4-pro"),
        documentation="https://api-docs.deepseek.com/quick_start/pricing/",
    ),
    dict(
        id="kimi",
        name="Kimi / 月之暗面",
        base_url="https://api.moonshot.cn/v1",
        models=("kimi-k3.1-pro", "kimi-k3.1-pro-highspeed", "kimi-k3"),
        documentation="https://platform.kimi.com/docs/models",
    ),
    dict(
        id="qwen",
        name="通义千问 / 阿里云百炼",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=("qwen3.8-flash", "qwen3.7-plus", "qwen4-max"),
        documentation="https://help.aliyun.com/zh/model-studio/models",
    ),
)


def preset(identity):
    return next((p for p in PRESETS if p["id"] == identity), None)
