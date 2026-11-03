"""Suite-wide test configuration."""

import os

from weavehr.utils.loader import AUTO_LOAD_ENV_VAR

# Tests populate the global config registries themselves; creating a step must not
# load the bundled configurations on top. Tests of lazy loading re-enable it.
os.environ[AUTO_LOAD_ENV_VAR] = "0"
