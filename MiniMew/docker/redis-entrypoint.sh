#!/bin/sh
set -eu
umask 077
: "${MINIMEW_REDIS_PASSWORD:?Redis password is required}"
# ACL 使用 SHA-256；原始密码不会出现在 Redis 命令行或配置文件中。
password_digest="$(printf '%s' "$MINIMEW_REDIS_PASSWORD" | sha256sum)"
printf 'user default on #%s ~* &* +@all\n' "${password_digest%% *}" > /tmp/minimew-redis.acl
chown redis:redis /tmp/minimew-redis.acl
unset MINIMEW_REDIS_PASSWORD
exec /usr/local/bin/docker-entrypoint.sh redis-server \
  --aclfile /tmp/minimew-redis.acl --appendonly yes --save '' --protected-mode yes
