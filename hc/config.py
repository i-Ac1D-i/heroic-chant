"""Server configuration.  Override any value with an HC_<NAME> env var."""
import os


def _env(name, default, cast=str):
    v = os.environ.get('HC_' + name)
    return cast(v) if v is not None else default


# Address the client is told to use for the game and match servers.  This must
# be reachable from the device, so on a phone set HC_PUBLIC_HOST to the LAN IP
# of this machine rather than leaving it as localhost.
PUBLIC_HOST = _env('PUBLIC_HOST', '127.0.0.1')
BIND_HOST = _env('BIND_HOST', '0.0.0.0')
PORT = _env('PORT', 21010, int)

# NGServerGroupInfo.ServerName is NOT a display string: AuthScene.OnGetServerGroup
# @0x1E906B4 feeds it straight to int.Parse, so it must be a numeric id into the
# hc_string_* tables.  34645 = "Global"; 31703 = "Republic of Korea";
# 35424 = "Japan"; 28395 = "TW/HK/MO".  A non-numeric value throws a
# FormatException in the client and login stalls with no visible error.
SERVER_NAME_STRING_ID = _env('SERVER_NAME_STRING_ID', 34645, int)
SERVER_GROUP_ID = _env('SERVER_GROUP_ID', 1, int)
CLIENT_VERSION = _env('CLIENT_VERSION', '1.2.389')
RESOURCE_VERSION = _env('RESOURCE_VERSION', 22, int)

# Language used when resolving NameIDs for log output.
STRING_LANG = _env('STRING_LANG', 'English')

LOG_LEVEL = _env('LOG_LEVEL', 'INFO')
