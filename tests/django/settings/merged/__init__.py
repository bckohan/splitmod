from splitmod import include, optional

include("components/base.py", scope=globals())
include("components/locale.py", scope=globals())
include("components/apps_middleware.py", scope=globals())
include("components/static.py", scope=globals())
include("components/templates.py", scope=globals())
include("components/database.py", scope=globals())
include("components/logging.py", scope=globals())

# Override settings for testing:
(optional("components/testing.py", scope=globals()),)

# Missing file:
(optional("components/missing_file.py", scope=globals()),)
