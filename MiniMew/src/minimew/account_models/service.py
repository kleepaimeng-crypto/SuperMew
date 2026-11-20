from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, select, update

from minimew.account_models.credentials import cipher, decrypt, encrypt
from minimew.account_models.schemas import AccountSettingsView, ModelView
from minimew.model_control import ModelConfigurationUnavailable, build_snapshot
from minimew.model_protocol import ModelConfiguration
from minimew.models import AccountModelConfiguration, AccountModelPreference, ModelCredential, User


class ModelNotFound(Exception):
    pass


async def preference(session, owner):
    # 与创建Run/设置写入共用账户锁；模型切换不会读到半个配置。
    if await session.scalar(select(User.id).where(User.id == owner).with_for_update()) is None:
        raise ModelNotFound
    item = await session.get(AccountModelPreference, owner)
    if item is None:
        item = AccountModelPreference(user_id=owner, model_id=None, knowledge_enabled=True)
        session.add(item)
        await session.flush()
    return item


async def owned(session, owner, identity):
    item = await session.scalar(
        select(AccountModelConfiguration).where(
            AccountModelConfiguration.id == identity,
            AccountModelConfiguration.user_id == owner,
            AccountModelConfiguration.deleted_at.is_(None),
        )
    )
    if item is None:
        raise ModelNotFound
    return item


def default_configuration(settings):
    from minimew.account_models.network import public_url

    return ModelConfiguration(
        provider="openai_compatible",
        adapter=settings.default_model_adapter,
        model_name=settings.default_model_name,
        base_url=public_url(settings.default_model_base_url),
        temperature=1.0 if settings.default_model_adapter == "kimi" else 0.2,
        max_output_tokens=settings.default_model_max_tokens,
        timeout_seconds=settings.default_model_timeout,
        thinking={
            "type": "native"
            if settings.default_model_adapter in {"kimi", "generic"}
            else "disabled"
        },
    )


async def freeze_account(session, owner, settings, credentials_available=True):
    item = await preference(session, owner)
    if item.model_id is None:
        if not credentials_available or not settings.model_credentials_available:
            raise ModelConfigurationUnavailable
        try:
            configuration = default_configuration(settings)
        except ValueError:
            raise ModelConfigurationUnavailable from None
        identity = uuid5(NAMESPACE_URL, "minimew:environment:" + configuration.model_dump_json())
        snapshot = build_snapshot(configuration, identity, 1)
        return snapshot, None, item.knowledge_enabled
    try:
        model = await owned(session, owner, item.model_id)
        configuration = ModelConfiguration.model_validate(model.configuration)
    except (ModelNotFound, ValueError):
        raise ModelConfigurationUnavailable from None
    credential = await session.get(ModelCredential, model.credential_id)
    if credential is None or credential.user_id != owner or credential.revoked_at is not None:
        raise ModelConfigurationUnavailable
    decrypt(settings, credential, configuration.base_url)
    return (
        build_snapshot(configuration, model.id, model.revision),
        credential.id,
        item.knowledge_enabled,
    )


async def write_model(session, owner, data, settings, identity=None):
    async with session.begin():
        await preference(session, owner)
        model = await owned(session, owner, identity) if identity else None
        configuration = data.configuration()
        key = data.api_key.get_secret_value() if data.api_key else ""
        if not key:
            if model is None or model.configuration.get("base_url") != configuration.base_url:
                raise ValueError("API Key is required for this endpoint")
            current = await session.get(ModelCredential, model.credential_id)
            if current is None or current.revoked_at is not None:
                raise ModelConfigurationUnavailable
            decrypt(settings, current, configuration.base_url)
            credential_id = current.id
        else:
            identity = identity or uuid4()
            credential_id = uuid4()
            session.add(
                ModelCredential(
                    id=credential_id,
                    user_id=owner,
                    model_id=identity,
                    encrypted_value=encrypt(
                        settings, owner, credential_id, configuration.base_url, key
                    ),
                )
            )
            await session.flush()
        if model is None:
            model = AccountModelConfiguration(
                id=identity,
                user_id=owner,
                revision=1,
                name=data.name,
                configuration=configuration.model_dump(mode="json"),
                credential_id=credential_id,
            )
            session.add(model)
        else:
            if (
                model.name == data.name
                and model.configuration == configuration.model_dump(mode="json")
                and model.credential_id == credential_id
            ):
                return view(model)
            model.revision += 1
            model.name, model.configuration, model.credential_id = (
                data.name,
                configuration.model_dump(mode="json"),
                credential_id,
            )
        await session.flush()
        return view(model)


def view(model):
    c = model.configuration
    return ModelView(
        id=model.id,
        name=model.name,
        revision=model.revision,
        adapter=c["adapter"],
        model_name=c["model_name"],
        base_url=c["base_url"],
        max_output_tokens=c["max_output_tokens"],
        timeout_seconds=c["timeout_seconds"],
        temperature=c["temperature"],
        key_configured=True,
    )


async def settings_view(session, owner, settings):
    async with session.begin():
        pref = await preference(session, owner)
        models = (
            await session.scalars(
                select(AccountModelConfiguration)
                .where(
                    AccountModelConfiguration.user_id == owner,
                    AccountModelConfiguration.deleted_at.is_(None),
                )
                .order_by(AccountModelConfiguration.name, AccountModelConfiguration.id)
            )
        ).all()
        try:
            cipher(settings)
            ready = True
        except ModelConfigurationUnavailable:
            ready = False
        return AccountSettingsView(
            models=[view(m) for m in models],
            current_model_id=pref.model_id,
            knowledge_enabled=pref.knowledge_enabled,
            knowledge_available=settings.knowledge_enabled,
            credential_storage_ready=ready,
            default_model_name=settings.default_model_name,
            default_configured=settings.model_credentials_available,
        )


async def select_model(session, owner, data):
    async with session.begin():
        pref = await preference(session, owner)
        if data.model_id:
            await owned(session, owner, data.model_id)
        pref.model_id, pref.knowledge_enabled = data.model_id, data.knowledge_enabled


async def delete_model(session, owner, identity):
    async with session.begin():
        pref = await preference(session, owner)
        model = await owned(session, owner, identity)
        model.deleted_at = await session.scalar(select(func.clock_timestamp()))
        if pref.model_id == identity:
            pref.model_id = None
        await session.execute(
            update(ModelCredential)
            .where(
                ModelCredential.user_id == owner,
                ModelCredential.model_id == identity,
                ModelCredential.revoked_at.is_(None),
            )
            .values(revoked_at=model.deleted_at)
        )
