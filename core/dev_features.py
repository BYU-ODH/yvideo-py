from django.conf import settings

DEMO_ADMIN_NETID = "devadmin"
DEMO_ADMIN_PASSWORD = "devadmin"
DEV_QUICK_LOGIN_HOSTS = {"127.0.0.1", "localhost"}
DEV_QUICK_LOGIN_HOSTS.update(getattr(settings, "DEV_QUICK_LOGIN_HOSTS", tuple()))


def is_dev_quick_login_enabled():
    return settings.DEBUG and getattr(settings, "DEV_QUICK_LOGIN_ENABLED", False)


def is_local_dev_host(host):
    return host.split(":", 1)[0] in DEV_QUICK_LOGIN_HOSTS
